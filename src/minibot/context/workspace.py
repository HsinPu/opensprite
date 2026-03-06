"""
minibot/workspace.py - 工作區輔助函式

提供以下功能：
- 取得工作區路徑（預設：~/.minibot/workspace）
- 從套件同步範本到工作區
- 載入啟動檔案（AGENTS.md、SOUL.md 等）
- 載入/儲存長期記憶（memory/MEMORY.md）
"""

from pathlib import Path


def get_workspace_path(workspace: str | None = None) -> Path:
    """Resolve and ensure workspace path. Defaults to ~/.minibot/workspace."""
    path = Path(workspace).expanduser() if workspace else Path.home() / ".minibot" / "workspace"
    return ensure_dir(path)


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def sync_templates(workspace: Path, silent: bool = False) -> list[str]:
    """Sync bundled templates to workspace. Only creates missing files."""
    try:
        from importlib.resources import files as pkg_files
        tpl = pkg_files("minibot") / "templates"
    except Exception:
        return []
    
    if not tpl.is_dir():
        return []

    added: list[str] = []

    def _write(src, dest: Path):
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8") if src else "", encoding="utf-8")
        added.append(str(dest.relative_to(workspace)))

    for item in tpl.iterdir():
        if item.name.endswith(".md"):
            _write(item, workspace / item.name)
    
    # Handle memory subdirectory
    memory_tpl = tpl / "memory"
    if memory_tpl.is_dir():
        memory_dir = workspace / "memory"
        memory_dir.mkdir(exist_ok=True)
        for item in memory_tpl.iterdir():
            if item.name.endswith(".md"):
                _write(item, memory_dir / item.name)

    if added and not silent:
        print(f"Created template files: {added}")
    
    return added


# Bootstrap files to load
BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "IDENTITY.md", "TOOLS.md"]


def load_bootstrap_files(workspace: Path) -> dict[str, str]:
    """
    Load bootstrap files from workspace.
    
    Returns dict with filename (without .md) as key, content as value.
    Empty string if file doesn't exist.
    """
    result = {}
    for filename in BOOTSTRAP_FILES:
        file_path = workspace / filename
        if file_path.exists():
            result[filename.replace(".md", "")] = file_path.read_text(encoding="utf-8")
        else:
            result[filename.replace(".md", "")] = ""
    return result


def load_memory(workspace: Path) -> str:
    """
    Load long-term memory from workspace.
    
    Returns content of memory/MEMORY.md, or empty string if not found.
    """
    memory_path = workspace / "memory" / "MEMORY.md"
    if memory_path.exists():
        return memory_path.read_text(encoding="utf-8")
    return ""


def save_memory(workspace: Path, content: str) -> None:
    """
    Save long-term memory to workspace.
    
    Creates directory and file if needed.
    """
    memory_path = workspace / "memory" / "MEMORY.md"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(content, encoding="utf-8")
