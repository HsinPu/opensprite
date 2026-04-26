"""Filesystem tools for reading and writing files."""

import asyncio
import difflib
import fnmatch
import hashlib
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from ..skills import SkillsLoader
from .base import Tool
from .skill_config import path_touches_read_only_app_skills_dir
from .validation import NON_EMPTY_STRING_PATTERN


WorkspaceResolver = Callable[[], Path]
ConfigPathResolver = Callable[[], Path | None]


_CONFIG_WRITE_GUARD_MSG = (
    "Error: Cannot modify OpenSprite configuration files with write_file, edit_file, or apply_patch. "
    "Edit them outside the agent or use `opensprite onboard`."
)
_DEFAULT_READ_LIMIT = 2000
_MAX_READ_LIMIT = 2000
_MAX_READ_CHARS = 50_000
_MAX_LINE_LENGTH = 2000
_SEARCH_RESULT_LIMIT = 100
_SEARCH_TIMEOUT_SECONDS = 30
_MAX_DIFF_CHARS = 12_000
_MAX_PATCH_CHANGES = 20
_SHA256_PATTERN = r"^[a-fA-F0-9]{64}$"


def path_touches_protected_system_config(
    file_path: Path,
    *,
    config_path_resolver: ConfigPathResolver | None = None,
) -> str | None:
    """Return an error message when write_file/edit_file must not touch system config."""
    try:
        resolved = file_path.resolve(strict=False)
    except OSError:
        return None

    blocked: frozenset[Path] | None = None
    if config_path_resolver is not None:
        cfg = config_path_resolver()
        if cfg is not None:
            try:
                from ..config.schema import Config

                blocked = Config.tool_write_blocked_paths(cfg)
            except Exception:
                blocked = None

            if blocked is not None and resolved in blocked:
                return _CONFIG_WRITE_GUARD_MSG

    if resolved.name.lower() == "opensprite.json":
        return _CONFIG_WRITE_GUARD_MSG

    return None


def _resolve_workspace_root(workspace: Path) -> Path:
    """Resolve and ensure the workspace root directory exists."""
    root = Path(workspace).expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_workspace_path(workspace: Path, path: str) -> Path | None:
    """Resolve a user path and return it only when it stays inside workspace."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate

    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(workspace)
    except ValueError:
        return None

    return candidate


def _build_workspace_resolver(
    workspace: Path | None = None,
    workspace_resolver: WorkspaceResolver | None = None,
) -> WorkspaceResolver:
    """Build a normalized workspace resolver."""
    if workspace_resolver is not None:
        return lambda: _resolve_workspace_root(workspace_resolver())

    if workspace is None:
        raise ValueError("workspace or workspace_resolver is required")

    root = _resolve_workspace_root(workspace)
    return lambda: root


def _write_guard(
    file_path: Path,
    *,
    config_path_resolver: ConfigPathResolver | None = None,
) -> str | None:
    prot_cfg = path_touches_protected_system_config(
        file_path,
        config_path_resolver=config_path_resolver,
    )
    if prot_cfg:
        return prot_cfg
    return path_touches_read_only_app_skills_dir(file_path)


def _display_path(workspace: Path, path: Path) -> str:
    """Return a stable workspace-relative path for tool output."""
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def _format_unified_diff(
    display_path: str,
    before: str | None,
    after: str | None,
) -> str:
    """Return a concise unified diff for user-visible tool results."""
    if before == after:
        return "(no changes)"

    before_text = before or ""
    after_text = after or ""
    fromfile = "/dev/null" if before is None else f"a/{display_path}"
    tofile = "/dev/null" if after is None else f"b/{display_path}"
    diff = "\n".join(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    if not diff:
        if before is None:
            diff = f"--- /dev/null\n+++ b/{display_path}\n@@\n(empty file created)"
        elif after is None:
            diff = f"--- a/{display_path}\n+++ /dev/null\n@@\n(empty file deleted)"
        else:
            diff = "(no changes)"
    if len(diff) > _MAX_DIFF_CHARS:
        return diff[:_MAX_DIFF_CHARS] + f"\n... (diff truncated, total {len(diff)} chars)"
    return diff


def _content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _hash_label(content: str | None) -> str:
    if content is None:
        return "missing"
    return _content_sha256(content)


def _format_file_metadata(display_path: str, before: str | None, after: str | None) -> str:
    return f"- {display_path}: before={_hash_label(before)}, after={_hash_label(after)}"


def _validate_expected_sha256(path: str, content: str, expected_sha256: Any) -> str | None:
    if expected_sha256 is None:
        return (
            f"Error: Stale-read guard failed for {path}: expected_sha256 is required when modifying an existing file. "
            "Read the file first and pass the SHA256 shown by read_file."
        )
    if not isinstance(expected_sha256, str):
        return f"Error: Stale-read guard failed for {path}: expected_sha256 must be a string."

    current_sha256 = _content_sha256(content)
    if expected_sha256.lower() != current_sha256:
        return (
            f"Error: Stale-read guard failed for {path}: current SHA256 is {current_sha256}, "
            f"but expected {expected_sha256}. Re-read the file before editing."
        )
    return None


def _read_existing_text(file_path: Path, path: str) -> str | None:
    if not file_path.exists():
        return None
    if not file_path.is_file():
        raise ValueError(f"Not a file: {path}")
    return file_path.read_text(encoding="utf-8")


def _truncate_line(line: str) -> str:
    if len(line) <= _MAX_LINE_LENGTH:
        return line
    return line[:_MAX_LINE_LENGTH] + f"... (line truncated to {_MAX_LINE_LENGTH} chars)"


def _matches_glob(path: str, pattern: str) -> bool:
    """Match glob patterns while treating **/ as zero or more directories."""
    if fnmatch.fnmatch(path, pattern):
        return True
    if "**/" in pattern and fnmatch.fnmatch(path, pattern.replace("**/", "")):
        return True
    return False


def _find_ripgrep() -> str | None:
    """Return the ripgrep executable path when it is available."""
    return shutil.which("rg")


async def _run_ripgrep(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run ripgrep without a shell and return decoded process output."""
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=_SEARCH_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")
        return 124, stdout_bytes.decode("utf-8", errors="replace"), stderr_text or "ripgrep timed out"
    return (
        int(process.returncode or 0),
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
    )


def _iter_workspace_files(workspace: Path, base: Path, include: str | None = None):
    """Yield files under base without following symlinks outside the workspace."""
    for file_path in base.rglob("*"):
        resolved = file_path.resolve(strict=False)
        try:
            relative_to_workspace = resolved.relative_to(workspace).as_posix()
        except ValueError:
            continue
        if not resolved.is_file():
            continue
        if include:
            relative_to_base = file_path.relative_to(base).as_posix()
            if (
                not _matches_glob(file_path.name, include)
                and not _matches_glob(relative_to_base, include)
                and not _matches_glob(relative_to_workspace, include)
            ):
                continue
        yield resolved


def _file_matches_include(workspace: Path, base: Path, file_path: Path, include: str | None) -> bool:
    """Return whether one file matches the optional include glob in supported path forms."""
    if not include:
        return True
    try:
        relative_to_base = file_path.relative_to(base).as_posix()
        relative_to_workspace = file_path.relative_to(workspace).as_posix()
    except ValueError:
        return False
    return (
        _matches_glob(file_path.name, include)
        or _matches_glob(relative_to_base, include)
        or _matches_glob(relative_to_workspace, include)
    )


async def _ripgrep_files(workspace: Path, search_path: Path, pattern: str) -> list[Path] | None:
    """Return rg-backed file matches, or None when rg is unavailable or fails unexpectedly."""
    rg = _find_ripgrep()
    if rg is None:
        return None

    search_arg = _display_path(workspace, search_path) or "."
    returncode, stdout, _stderr = await _run_ripgrep(
        [rg, "--files", "--no-messages", "--", search_arg],
        workspace,
    )
    if returncode not in (0, 1):
        return None

    matches: list[Path] = []
    for raw_line in stdout.splitlines():
        relative_path = raw_line.strip()
        if not relative_path:
            continue
        file_path = _resolve_workspace_path(workspace, relative_path)
        if file_path is None or not file_path.is_file():
            continue
        if _file_matches_include(workspace, search_path, file_path, pattern):
            matches.append(file_path)
    return matches


async def _ripgrep_search(
    workspace: Path,
    search_path: Path,
    pattern: str,
    include: str | None,
) -> tuple[list[tuple[float, str, int, str]] | None, str | None]:
    """Return rg-backed grep matches plus an optional user-facing error."""
    rg = _find_ripgrep()
    if rg is None:
        return None, None

    args = [
        rg,
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        "--no-messages",
        "-e",
        pattern,
    ]
    if include:
        args.extend(["--glob", include])
    search_arg = _display_path(workspace, search_path) or "."
    args.extend(["--", search_arg])

    returncode, stdout, stderr = await _run_ripgrep(args, workspace)
    if returncode == 1:
        return [], None
    if returncode != 0:
        message = stderr.strip().splitlines()[0] if stderr.strip() else "ripgrep failed"
        return [], f"Error: Invalid regex: {message}"

    matches: list[tuple[float, str, int, str]] = []
    for raw_line in stdout.splitlines():
        path_part, separator, rest = raw_line.partition(":")
        if not separator:
            continue
        line_no_part, separator, line = rest.partition(":")
        if not separator:
            continue
        try:
            line_no = int(line_no_part)
        except ValueError:
            continue

        file_path = _resolve_workspace_path(workspace, path_part)
        if file_path is None or not file_path.is_file():
            continue
        if not _file_matches_include(workspace, search_path, file_path, include):
            continue
        matches.append(
            (
                file_path.stat().st_mtime,
                _display_path(workspace, file_path),
                line_no,
                _truncate_line(line),
            )
        )
    return matches, None


class ReadFileTool(Tool):
    """Tool to read file contents."""

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
        skills_loader: SkillsLoader | None = None,
    ):
        self._workspace_resolver = _build_workspace_resolver(workspace, workspace_resolver)
        self.skills_loader = skills_loader

    def _get_workspace(self) -> Path:
        return self._workspace_resolver()

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of one file inside the current workspace. "
            "Always provide a non-empty 'path'. Supports optional 1-indexed 'offset' and 'limit' for large files. "
            "Output is line-numbered for precise follow-up edits. Use read_skill instead of reading SKILL.md files directly when possible."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Required. File path to read, relative to the current workspace unless already absolute inside it.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "offset": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional. 1-indexed line number to start reading from. Defaults to 1.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_READ_LIMIT,
                    "description": f"Optional. Maximum number of lines to read. Defaults to {_DEFAULT_READ_LIMIT}.",
                },
            },
            "required": ["path"]
        }

    async def _execute(self, **kwargs: Any) -> str:
        try:
            path = str(kwargs["path"])
            workspace = self._get_workspace()
            file_path = _resolve_workspace_path(workspace, path)
            if file_path is None:
                return f"Error: Access denied. Path must be within workspace: {workspace}"
            
            # Check if reading a skill file -> redirect to read_skill
            if self.skills_loader and file_path.name == "SKILL.md":
                path_str = str(file_path)
                if "/skills/" in path_str or "\\skills\\" in path_str:
                    personal_skills_dir = workspace / "skills"
                    parts = file_path.parts
                    for i, part in enumerate(parts):
                        if part == "skills" and i + 1 < len(parts):
                            skill_name = parts[i + 1]
                            if self.skills_loader.skill_exists(skill_name, personal_skills_dir):
                                content = self.skills_loader.load_skill_content(skill_name, personal_skills_dir)
                                return f"[Note: Use read_skill tool instead]\n\n{content}"
            
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"
            
            offset = int(kwargs.get("offset", 1))
            limit = int(kwargs.get("limit", _DEFAULT_READ_LIMIT))
            content = file_path.read_text(encoding="utf-8")
            lines = content.splitlines()
            total_lines = len(lines)
            if total_lines and offset > total_lines:
                return f"Error: Offset {offset} is out of range for {path} ({total_lines} lines)."

            start = offset - 1
            selected: list[str] = []
            used_chars = 0
            truncated_by_chars = False
            for line_no, line in enumerate(lines[start:start + limit], start=offset):
                rendered = f"{line_no}: {_truncate_line(line)}"
                if selected and used_chars + len(rendered) + 1 > _MAX_READ_CHARS:
                    truncated_by_chars = True
                    break
                selected.append(rendered)
                used_chars += len(rendered) + 1

            last_line = offset + len(selected) - 1
            has_more = total_lines > last_line or truncated_by_chars
            header = [
                f"File: {_display_path(workspace, file_path)}",
                f"SHA256: {_content_sha256(content)}",
                f"Lines: {offset}-{last_line if selected else 0} of {total_lines}",
                "",
            ]
            if not selected:
                header.append("(empty file)")
                return "\n".join(header)

            output = [*header, *selected]
            if has_more:
                output.extend([
                    "",
                    f"(Showing lines {offset}-{last_line} of {total_lines}. Use offset={last_line + 1} to continue.)",
                ])
            else:
                output.extend(["", f"(End of file - total {total_lines} lines)"])
            return "\n".join(output)
        except Exception as e:
            return f"Error reading file: {str(e)}"


class GlobFilesTool(Tool):
    """Tool to find files by glob pattern."""

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
    ):
        self._workspace_resolver = _build_workspace_resolver(workspace, workspace_resolver)

    def _get_workspace(self) -> Path:
        return self._workspace_resolver()

    @property
    def name(self) -> str:
        return "glob_files"

    @property
    def description(self) -> str:
        return (
            "Find files inside the current workspace using ripgrep-backed glob matching such as '**/*.py' or 'src/**/*.md'. "
            "Use this before reading when you are unsure of exact file paths."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Required. Glob pattern to match files inside the search path.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "path": {
                    "type": "string",
                    "description": "Optional. Directory to search, relative to the current workspace. Defaults to '.'.",
                },
            },
            "required": ["pattern"],
        }

    async def _execute(self, **kwargs: Any) -> str:
        try:
            pattern = str(kwargs["pattern"])
            path = str(kwargs.get("path", "."))
            workspace = self._get_workspace()
            search_path = _resolve_workspace_path(workspace, path)
            if search_path is None:
                return f"Error: Access denied. Path must be within workspace: {workspace}"
            if not search_path.exists():
                return f"Error: Directory not found: {path}"
            if not search_path.is_dir():
                return f"Error: Not a directory: {path}"

            matches = await _ripgrep_files(workspace, search_path, pattern)
            if matches is None:
                matches = []
                for item in search_path.glob(pattern):
                    item = item.resolve(strict=False)
                    try:
                        item.relative_to(workspace)
                    except ValueError:
                        continue
                    if item.is_file():
                        matches.append(item)
            matches.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            truncated = len(matches) > _SEARCH_RESULT_LIMIT
            matches = matches[:_SEARCH_RESULT_LIMIT]
            if not matches:
                return "No files found"

            output = [_display_path(workspace, item) for item in matches]
            if truncated:
                output.extend([
                    "",
                    f"(Results truncated: showing first {_SEARCH_RESULT_LIMIT} files. Use a more specific pattern.)",
                ])
            return "\n".join(output)
        except Exception as e:
            return f"Error finding files: {str(e)}"


class GrepFilesTool(Tool):
    """Tool to search file contents by regular expression."""

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
    ):
        self._workspace_resolver = _build_workspace_resolver(workspace, workspace_resolver)

    def _get_workspace(self) -> Path:
        return self._workspace_resolver()

    @property
    def name(self) -> str:
        return "grep_files"

    @property
    def description(self) -> str:
        return (
            "Search text files inside the current workspace using ripgrep-backed regular expressions. "
            "Supports optional 'path' and 'include' glob filters such as '*.py' or 'src/**/*.py'."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Required. Regular expression to search for.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "path": {
                    "type": "string",
                    "description": "Optional. Directory to search, relative to the current workspace. Defaults to '.'.",
                },
                "include": {
                    "type": "string",
                    "description": "Optional. File glob filter such as '*.py' or 'src/**/*.py'.",
                },
            },
            "required": ["pattern"],
        }

    async def _execute(self, **kwargs: Any) -> str:
        try:
            pattern = str(kwargs["pattern"])
            path = str(kwargs.get("path", "."))
            include = kwargs.get("include")
            include = str(include) if include else None
            workspace = self._get_workspace()
            search_path = _resolve_workspace_path(workspace, path)
            if search_path is None:
                return f"Error: Access denied. Path must be within workspace: {workspace}"
            if not search_path.exists():
                return f"Error: Directory not found: {path}"
            if not search_path.is_dir():
                return f"Error: Not a directory: {path}"

            matches, rg_error = await _ripgrep_search(workspace, search_path, pattern, include)
            if rg_error is not None:
                return rg_error
            if matches is None:
                try:
                    regex = re.compile(pattern)
                except re.error as e:
                    return f"Error: Invalid regex: {e}"
                matches = []
                for file_path in _iter_workspace_files(workspace, search_path, include):
                    try:
                        text = file_path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        continue
                    except OSError:
                        continue
                    for line_no, line in enumerate(text.splitlines(), start=1):
                        if not regex.search(line):
                            continue
                        matches.append(
                            (
                                file_path.stat().st_mtime,
                                _display_path(workspace, file_path),
                                line_no,
                                _truncate_line(line),
                            )
                        )
            total = len(matches)

            if total == 0:
                return "No files found"

            matches.sort(key=lambda item: item[0], reverse=True)
            shown_matches = matches[:_SEARCH_RESULT_LIMIT]
            output = [
                f"Found {total} matches"
                + (f" (showing first {_SEARCH_RESULT_LIMIT})" if total > _SEARCH_RESULT_LIMIT else "")
            ]
            current_file = ""
            for _, file_name, line_no, line in shown_matches:
                if file_name != current_file:
                    if current_file:
                        output.append("")
                    current_file = file_name
                    output.append(f"{file_name}:")
                output.append(f"  Line {line_no}: {line}")
            if total > _SEARCH_RESULT_LIMIT:
                output.extend([
                    "",
                    f"(Results truncated: showing {_SEARCH_RESULT_LIMIT} of {total} matches. Use a more specific path, include, or pattern.)",
                ])
            return "\n".join(output)
        except Exception as e:
            return f"Error searching files: {str(e)}"


class ApplyPatchTool(Tool):
    """Tool to apply one or more structured text file changes."""

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
        config_path_resolver: ConfigPathResolver | None = None,
    ):
        self._workspace_resolver = _build_workspace_resolver(workspace, workspace_resolver)
        self._config_path_resolver = config_path_resolver

    def _get_workspace(self) -> Path:
        return self._workspace_resolver()

    @property
    def name(self) -> str:
        return "apply_patch"

    @property
    def description(self) -> str:
        return (
            "Apply a structured patch inside the current workspace. Use this for multi-file code edits. "
            "Each change action is 'add', 'update', or 'delete'. Updates require exact old_text and refuse ambiguous matches. "
            "For update/delete of existing files, provide expected_sha256 from the latest read_file output. "
            "The tool validates all changes before writing and returns unified diffs."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "changes": {
                    "type": "array",
                    "description": f"Required. Ordered list of up to {_MAX_PATCH_CHANGES} file changes to apply.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["add", "update", "delete"],
                                "description": "Required. Patch action for this file change.",
                            },
                            "path": {
                                "type": "string",
                                "pattern": NON_EMPTY_STRING_PATTERN,
                                "description": "Required. File path inside the workspace.",
                            },
                            "old_text": {
                                "type": "string",
                                "description": "Required for update. Exact existing text to replace; must appear exactly once.",
                            },
                            "new_text": {
                                "type": "string",
                                "description": "Required for update. Replacement text.",
                            },
                            "content": {
                                "type": "string",
                                "description": "Required for add. Complete file content for the new file.",
                            },
                            "expected_sha256": {
                                "type": "string",
                                "pattern": _SHA256_PATTERN,
                                "description": "Required for update/delete of an existing file. SHA256 shown by the latest read_file output.",
                            },
                        },
                        "required": ["action", "path"],
                    },
                }
            },
            "required": ["changes"],
        }

    async def _execute(self, **kwargs: Any) -> str:
        try:
            changes = kwargs["changes"]
            if not changes:
                return "Error: changes must contain at least one file change."
            if len(changes) > _MAX_PATCH_CHANGES:
                return f"Error: apply_patch supports at most {_MAX_PATCH_CHANGES} changes per call."

            workspace = self._get_workspace()
            original: dict[Path, str | None] = {}
            current: dict[Path, str | None] = {}
            display_paths: dict[Path, str] = {}

            def load_state(file_path: Path, path: str) -> str | None:
                if file_path not in current:
                    original[file_path] = _read_existing_text(file_path, path)
                    current[file_path] = original[file_path]
                    display_paths[file_path] = _display_path(workspace, file_path)
                return current[file_path]

            for index, change in enumerate(changes, start=1):
                action = str(change["action"])
                path = str(change["path"])
                file_path = _resolve_workspace_path(workspace, path)
                if file_path is None:
                    return f"Error: Change {index}: access denied. Path must be within workspace: {workspace}"

                guard = _write_guard(file_path, config_path_resolver=self._config_path_resolver)
                if guard:
                    return guard

                existing = load_state(file_path, path)

                if action == "add":
                    if existing is not None:
                        return f"Error: Change {index}: file already exists: {path}"
                    content = change.get("content")
                    if not isinstance(content, str):
                        return f"Error: Change {index}: add requires string content."
                    current[file_path] = content
                    continue

                if action == "update":
                    if existing is None:
                        return f"Error: Change {index}: file not found: {path}"
                    if original[file_path] is not None:
                        stale_error = _validate_expected_sha256(path, original[file_path], change.get("expected_sha256"))
                        if stale_error:
                            return f"Error: Change {index}: {stale_error.removeprefix('Error: ')}"
                    old_text = change.get("old_text")
                    new_text = change.get("new_text")
                    if not isinstance(old_text, str) or not old_text:
                        return f"Error: Change {index}: update requires non-empty string old_text."
                    if not isinstance(new_text, str):
                        return f"Error: Change {index}: update requires string new_text."
                    count = existing.count(old_text)
                    if count == 0:
                        return f"Error: Change {index}: old_text not found in {path}."
                    if count > 1:
                        return f"Error: Change {index}: old_text appears {count} times in {path}. Provide more context."
                    current[file_path] = existing.replace(old_text, new_text, 1)
                    continue

                if action == "delete":
                    if existing is None:
                        return f"Error: Change {index}: file not found: {path}"
                    if original[file_path] is not None:
                        stale_error = _validate_expected_sha256(path, original[file_path], change.get("expected_sha256"))
                        if stale_error:
                            return f"Error: Change {index}: {stale_error.removeprefix('Error: ')}"
                    current[file_path] = None

            diffs: list[str] = []
            metadata: list[str] = []
            changed_paths: list[Path] = []
            for file_path, after in current.items():
                before = original[file_path]
                if before == after:
                    continue
                changed_paths.append(file_path)
                metadata.append(_format_file_metadata(display_paths[file_path], before, after))
                diffs.append(_format_unified_diff(display_paths[file_path], before, after))

            if not changed_paths:
                return "No changes to apply."

            for file_path in changed_paths:
                after = current[file_path]
                if after is None:
                    file_path.unlink()
                else:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(after, encoding="utf-8")

            return (
                f"Successfully applied patch ({len(changed_paths)} file(s) changed)\n\n"
                "File Versions:\n"
                + "\n".join(metadata)
                + "\n\nDiff:\n"
                + "\n\n".join(diffs)
            )
        except ValueError as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error applying patch: {str(e)}"


class WriteFileTool(Tool):
    """Tool to write content to a file."""

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
        config_path_resolver: ConfigPathResolver | None = None,
    ):
        self._workspace_resolver = _build_workspace_resolver(workspace, workspace_resolver)
        self._config_path_resolver = config_path_resolver

    def _get_workspace(self) -> Path:
        return self._workspace_resolver()

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write content to one file inside the current workspace. "
            "Always provide both 'path' and 'content'. Creates parent directories and the file if needed. "
            "When replacing an existing file, provide expected_sha256 from the latest read_file output. "
            "Returns a unified diff after writing. "
            "Cannot write under ~/.opensprite/skills/ (read-only bundled skills); use session workspace skills/ or configure_skill. "
            "Cannot write opensprite.json, split JSON config files (channels, search, MCP, media, LLM providers), "
            "or the active config paths (edit outside the agent or use onboard)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Required. Target file path inside the current workspace.",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                },
                "content": {
                    "type": "string",
                    "description": "Required. Complete file contents to write at the target path."
                },
                "expected_sha256": {
                    "type": "string",
                    "pattern": _SHA256_PATTERN,
                    "description": "Required when replacing an existing file. SHA256 shown by the latest read_file output."
                }
            },
            "required": ["path", "content"]
        }

    async def _execute(self, **kwargs: Any) -> str:
        try:
            path = str(kwargs["path"])
            content = str(kwargs["content"])
            workspace = self._get_workspace()
            file_path = _resolve_workspace_path(workspace, path)
            if file_path is None:
                return f"Error: Access denied. Path must be within workspace: {workspace}"

            guard = _write_guard(file_path, config_path_resolver=self._config_path_resolver)
            if guard:
                return guard

            before = _read_existing_text(file_path, path)
            if before is not None:
                stale_error = _validate_expected_sha256(path, before, kwargs.get("expected_sha256"))
                if stale_error:
                    return stale_error
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            diff = _format_unified_diff(_display_path(workspace, file_path), before, content)
            metadata = _format_file_metadata(_display_path(workspace, file_path), before, content)
            return f"Successfully wrote to {path} ({len(content)} chars)\n\nFile Versions:\n{metadata}\n\nDiff:\n{diff}"
        except Exception as e:
            return f"Error writing file: {str(e)}"


class ListDirTool(Tool):
    """Tool to list directory contents."""

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
    ):
        self._workspace_resolver = _build_workspace_resolver(workspace, workspace_resolver)

    def _get_workspace(self) -> Path:
        return self._workspace_resolver()

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List files and directories in a given path."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The directory path to list (default: current directory)"
                }
            }
        }

    async def _execute(self, **kwargs: Any) -> str:
        try:
            path = str(kwargs.get("path", "."))
            workspace = self._get_workspace()
            dir_path = _resolve_workspace_path(workspace, path)
            if dir_path is None:
                return f"Error: Access denied. Path must be within workspace: {workspace}"
            
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"
            
            if not dir_path.is_dir():
                return f"Error: Not a directory: {path}"
            
            items = []
            for item in sorted(dir_path.iterdir()):
                suffix = "/" if item.is_dir() else ""
                items.append(f"{item.name}{suffix}")
            
            return "\n".join(items) if items else "(empty)"
        except Exception as e:
            return f"Error listing directory: {str(e)}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text."""

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
        config_path_resolver: ConfigPathResolver | None = None,
    ):
        self._workspace_resolver = _build_workspace_resolver(workspace, workspace_resolver)
        self._config_path_resolver = config_path_resolver

    def _get_workspace(self) -> Path:
        return self._workspace_resolver()

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit one file inside the current workspace by replacing 'old_text' with 'new_text'. "
            "Always provide 'path', 'old_text', and 'new_text'. The old_text must match existing file content exactly. "
            "Provide expected_sha256 from the latest read_file output so stale reads cannot overwrite newer changes. "
            "Returns a unified diff after editing. "
            "Cannot edit under ~/.opensprite/skills/ (read-only bundled skills). "
            "Cannot edit opensprite.json or other OpenSprite JSON configuration files."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Required. Target file path inside the current workspace.", "pattern": NON_EMPTY_STRING_PATTERN},
                "old_text": {"type": "string", "description": "Required. Exact existing text to replace. It must appear exactly once or the tool will refuse the edit.", "minLength": 1},
                "new_text": {"type": "string", "description": "Required. Replacement text for the matching old_text."},
                "expected_sha256": {"type": "string", "pattern": _SHA256_PATTERN, "description": "Required. SHA256 shown by the latest read_file output."},
            },
            "required": ["path", "old_text", "new_text"],
        }

    async def _execute(self, **kwargs: Any) -> str:
        try:
            path = str(kwargs["path"])
            old_text = str(kwargs["old_text"])
            new_text = str(kwargs["new_text"])
            workspace = self._get_workspace()
            file_path = _resolve_workspace_path(workspace, path)
            if file_path is None:
                return f"Error: Access denied. Path must be within workspace: {workspace}"

            guard = _write_guard(file_path, config_path_resolver=self._config_path_resolver)
            if guard:
                return guard

            if not file_path.exists():
                return f"Error: File not found: {path}"

            content = file_path.read_text(encoding="utf-8")
            stale_error = _validate_expected_sha256(path, content, kwargs.get("expected_sha256"))
            if stale_error:
                return stale_error

            if old_text not in content:
                return f"Error: old_text not found in file. Please provide exact text to replace."

            # Count occurrences
            count = content.count(old_text)
            if count > 1:
                return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

            new_content = content.replace(old_text, new_text, 1)
            file_path.write_text(new_content, encoding="utf-8")
            diff = _format_unified_diff(_display_path(workspace, file_path), content, new_content)
            metadata = _format_file_metadata(_display_path(workspace, file_path), content, new_content)

            return f"Successfully edited {path}\n\nFile Versions:\n{metadata}\n\nDiff:\n{diff}"
        except Exception as e:
            return f"Error editing file: {str(e)}"
