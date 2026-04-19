"""Filesystem tools for reading and writing files."""

from pathlib import Path
from typing import Any, Callable

from ..skills import SkillsLoader
from .base import Tool
from .skill_config import path_touches_protected_system_skill
from .validation import NON_EMPTY_STRING_PATTERN


WorkspaceResolver = Callable[[], Path]
ConfigPathResolver = Callable[[], Path | None]


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
    if resolved.name.lower() == "opensprite.json":
        return (
            "Error: Cannot modify opensprite.json with write_file or edit_file. "
            "Edit ~/.opensprite/opensprite.json (or your configured config path) outside the agent, "
            "or use `opensprite onboard`."
        )
    if config_path_resolver is not None:
        cfg = config_path_resolver()
        if cfg is not None:
            try:
                cfg_resolved = Path(cfg).expanduser().resolve(strict=False)
                if resolved == cfg_resolved:
                    return (
                        "Error: Cannot modify the OpenSprite main configuration file with write_file or edit_file. "
                        "Edit it outside the agent or use `opensprite onboard`."
                    )
            except OSError:
                pass
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
            "Always provide a non-empty 'path'. Use read_skill instead of reading SKILL.md files directly when possible."
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
                }
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
            
            content = file_path.read_text(encoding="utf-8")
            # Limit output size
            if len(content) > 5000:
                content = content[:5000] + f"\n\n... (truncated, total {len(content)} chars)"
            return content
        except Exception as e:
            return f"Error reading file: {str(e)}"


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
            "Cannot write under skills/<bundled_system_skill_id>/ (read-only); use read_skill for those skills. "
            "Cannot write opensprite.json or the active main config path (edit outside the agent or use onboard)."
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

            prot_cfg = path_touches_protected_system_config(
                file_path, config_path_resolver=self._config_path_resolver
            )
            if prot_cfg:
                return prot_cfg

            prot = path_touches_protected_system_skill(file_path)
            if prot:
                return prot

            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote to {path} ({len(content)} chars)"
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
            "Cannot edit under skills/<bundled_system_skill_id>/ (read-only). "
            "Cannot edit opensprite.json or the active main config path."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Required. Target file path inside the current workspace.", "pattern": NON_EMPTY_STRING_PATTERN},
                "old_text": {"type": "string", "description": "Required. Exact existing text to replace. It must appear exactly once or the tool will refuse the edit.", "minLength": 1},
                "new_text": {"type": "string", "description": "Required. Replacement text for the matching old_text."},
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

            prot_cfg = path_touches_protected_system_config(
                file_path, config_path_resolver=self._config_path_resolver
            )
            if prot_cfg:
                return prot_cfg

            prot = path_touches_protected_system_skill(file_path)
            if prot:
                return prot

            if not file_path.exists():
                return f"Error: File not found: {path}"

            content = file_path.read_text(encoding="utf-8")

            if old_text not in content:
                return f"Error: old_text not found in file. Please provide exact text to replace."

            # Count occurrences
            count = content.count(old_text)
            if count > 1:
                return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

            new_content = content.replace(old_text, new_text, 1)
            file_path.write_text(new_content, encoding="utf-8")

            return f"Successfully edited {path}"
        except Exception as e:
            return f"Error editing file: {str(e)}"
