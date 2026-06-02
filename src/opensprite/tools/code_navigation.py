"""Static code navigation helpers for workspace-bounded source lookup."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .base import Tool
from .result_status import tool_error_result


WorkspaceResolver = Callable[[], Path]

_MAX_FILES = 300
_MAX_RESULTS = 100
_MAX_FILE_CHARS = 200_000
_TOOL_NAME = "code_navigation"
_SYMBOL_RE = re.compile(
    r"^\s*(?:"
    r"(?P<py>class\s+(?P<py_class>[A-Za-z_][\w]*)|def\s+(?P<py_func>[A-Za-z_][\w]*))"
    r"|(?P<js>(?:export\s+)?(?:async\s+)?function\s+(?P<js_func>[A-Za-z_$][\w$]*)|(?:export\s+)?class\s+(?P<js_class>[A-Za-z_$][\w$]*))"
    r"|(?P<const>(?:export\s+)?(?:const|let|var)\s+(?P<const_name>[A-Za-z_$][\w$]*)\s*=)"
    r")"
)
_SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".java",
    ".go",
    ".rs",
    ".css",
    ".html",
    ".md",
}


def _resolve_workspace_root(workspace: Path) -> Path:
    root = Path(workspace).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _build_workspace_resolver(
    workspace: Path | None = None,
    workspace_resolver: WorkspaceResolver | None = None,
) -> WorkspaceResolver:
    if workspace_resolver is not None:
        return lambda: _resolve_workspace_root(workspace_resolver())
    if workspace is None:
        raise ValueError("workspace or workspace_resolver is required")
    root = _resolve_workspace_root(workspace)
    return lambda: root


def _resolve_workspace_path(workspace: Path, path: str) -> Path | None:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None
    return candidate


def _display_path(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def _read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > _MAX_FILE_CHARS:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _iter_source_files(workspace: Path, root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if len(files) >= _MAX_FILES:
            break
        if not path.is_file() or path.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        try:
            path.relative_to(workspace)
        except ValueError:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.as_posix())


def _symbol_from_line(line: str) -> tuple[str, str] | None:
    match = _SYMBOL_RE.match(line)
    if not match:
        return None
    name = (
        match.group("py_class")
        or match.group("py_func")
        or match.group("js_class")
        or match.group("js_func")
        or match.group("const_name")
    )
    if not name:
        return None
    kind = "class" if match.group("py_class") or match.group("js_class") else "function"
    if match.group("const_name"):
        kind = "variable"
    return name, kind


def _document_symbols(workspace: Path, path: Path) -> list[dict[str, Any]]:
    text = _read_text(path)
    if text is None:
        return []
    symbols = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        parsed = _symbol_from_line(line)
        if parsed is None:
            continue
        name, kind = parsed
        symbols.append({"name": name, "kind": kind, "path": _display_path(workspace, path), "line": line_number})
    return symbols


def _line_matches_symbol(line: str, symbol: str) -> bool:
    return re.search(rf"\b{re.escape(symbol)}\b", line) is not None


def _code_navigation_error_result(
    error: str,
    *,
    category: str,
    error_type: str = "CodeNavigationToolError",
    invalid_arguments: bool = False,
) -> str:
    return tool_error_result(
        error,
        error_type=error_type,
        category=category,
        repeated_error_key=error if invalid_arguments else None,
        invalid_arguments=invalid_arguments,
        metadata={"tool_name": _TOOL_NAME},
    )


class CodeNavigationTool(Tool):
    """Static code navigation fallback for symbols, definitions, and references."""

    def __init__(self, workspace: Path | None = None, workspace_resolver: WorkspaceResolver | None = None):
        self._workspace_resolver = _build_workspace_resolver(workspace, workspace_resolver)

    @property
    def name(self) -> str:
        return _TOOL_NAME

    @property
    def description(self) -> str:
        return "Navigate code inside the workspace: document_symbols, workspace_symbols, go_to_definition, references."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["document_symbols", "workspace_symbols", "go_to_definition", "references"],
                },
                "path": {"type": "string", "description": "Workspace-relative file or directory path."},
                "symbol": {"type": "string", "description": "Symbol name for workspace lookup."},
            },
            "required": ["action"],
        }

    async def _execute(self, action: str, path: str = ".", symbol: str = "", **kwargs: Any) -> str:
        workspace = self._workspace_resolver()
        target = _resolve_workspace_path(workspace, path or ".")
        if target is None:
            return _code_navigation_error_result(
                "Access denied. Path must stay inside the workspace.",
                category="access_denied",
            )

        if action == "document_symbols":
            if not target.is_file():
                return _code_navigation_error_result(
                    "document_symbols requires a file path.",
                    category="invalid_arguments",
                    error_type="ToolValidationError",
                    invalid_arguments=True,
                )
            result = {"action": action, "symbols": _document_symbols(workspace, target)}
            return json.dumps(result, ensure_ascii=False, indent=2)

        files = _iter_source_files(workspace, target if target.is_dir() else target.parent)
        if action == "workspace_symbols":
            symbols = []
            needle = symbol.strip().lower()
            for file_path in files:
                for item in _document_symbols(workspace, file_path):
                    if not needle or needle in item["name"].lower():
                        symbols.append(item)
                    if len(symbols) >= _MAX_RESULTS:
                        break
                if len(symbols) >= _MAX_RESULTS:
                    break
            return json.dumps({"action": action, "symbols": symbols}, ensure_ascii=False, indent=2)

        if not symbol.strip():
            return _code_navigation_error_result(
                f"{action} requires symbol.",
                category="invalid_arguments",
                error_type="ToolValidationError",
                invalid_arguments=True,
            )

        if action == "go_to_definition":
            definitions = []
            for file_path in files:
                for item in _document_symbols(workspace, file_path):
                    if item["name"] == symbol:
                        definitions.append(item)
                if definitions:
                    break
            return json.dumps({"action": action, "definitions": definitions}, ensure_ascii=False, indent=2)

        if action == "references":
            references = []
            for file_path in files:
                text = _read_text(file_path)
                if text is None:
                    continue
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if _line_matches_symbol(line, symbol):
                        references.append(
                            {
                                "path": _display_path(workspace, file_path),
                                "line": line_number,
                                "preview": line.strip()[:240],
                            }
                        )
                    if len(references) >= _MAX_RESULTS:
                        break
                if len(references) >= _MAX_RESULTS:
                    break
            return json.dumps({"action": action, "references": references}, ensure_ascii=False, indent=2)

        return _code_navigation_error_result(
            f"Unsupported code_navigation action: {action}",
            category="invalid_arguments",
            error_type="ToolValidationError",
            invalid_arguments=True,
        )
