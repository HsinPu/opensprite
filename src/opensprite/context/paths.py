"""
opensprite/context/paths.py - Path helpers and template sync.

Path layout:
- app home: ~/.opensprite
- subagent prompts: ~/.opensprite/subagent_prompts/*.md (seeded from bundled templates on first sync)
- bootstrap files: ~/.opensprite/bootstrap/*.md
- memory: ~/.opensprite/memory/<chat>/MEMORY.md
- recent summary: ~/.opensprite/memory/<chat>/RECENT_SUMMARY.md
- bundled skills (read-only, synced from package): ~/.opensprite/skills/<skill_id>/SKILL.md
- session workspace skills (mutable): ~/.opensprite/workspace/chats/{channel}/{chat_id}/skills/*/SKILL.md
- workspace root: ~/.opensprite/workspace
- per-chat workspaces: ~/.opensprite/workspace/chats/{channel}/{chat_id}
"""

import hashlib
import logging
import re
import shutil
from pathlib import Path


logger = logging.getLogger(__name__)

OPENSPRITE_HOME = Path.home() / ".opensprite"
BOOTSTRAP_DIRNAME = "bootstrap"
MEMORY_DIRNAME = "memory"
SKILLS_DIRNAME = "skills"
WORKSPACE_DIRNAME = "workspace"
WORKSPACE_CHATS_DIRNAME = "chats"
SUBAGENT_PROMPTS_DIRNAME = "subagent_prompts"
USER_PROFILE_STATE_FILENAME = ".user_profile_state.json"
RECENT_SUMMARY_STATE_FILENAME = ".recent_summary_state.json"

BOOTSTRAP_FILES = ["SOUL.md", "IDENTITY.md", "AGENTS.md", "USER.md", "TOOLS.md"]


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_app_home(app_home: str | Path | None = None) -> Path:
    """Resolve the OpenSprite app home directory."""
    path = Path(app_home).expanduser() if app_home else OPENSPRITE_HOME
    return ensure_dir(path)


def get_bootstrap_dir(app_home: str | Path | None = None) -> Path:
    """Get the bootstrap directory that stores startup markdown files."""
    return ensure_dir(get_app_home(app_home) / BOOTSTRAP_DIRNAME)


def get_user_profile_file(app_home: str | Path | None = None) -> Path:
    """Get the global USER.md profile file path."""
    return get_bootstrap_dir(app_home) / "USER.md"


def get_user_profile_state_file(app_home: str | Path | None = None) -> Path:
    """Get the persisted state file for USER.md auto-updates."""
    return get_bootstrap_dir(app_home) / USER_PROFILE_STATE_FILENAME


def get_memory_dir(app_home: str | Path | None = None) -> Path:
    """Get the long-term memory directory."""
    return ensure_dir(get_app_home(app_home) / MEMORY_DIRNAME)


def get_skills_dir(app_home: str | Path | None = None) -> Path:
    """Get the app-home bundled skills directory (~/.opensprite/skills/<skill_id>/)."""
    return ensure_dir(get_app_home(app_home) / SKILLS_DIRNAME)


def get_tool_workspace(app_home: str | Path | None = None) -> Path:
    """Get the shared root directory that contains per-chat workspaces."""
    return ensure_dir(get_app_home(app_home) / WORKSPACE_DIRNAME)


def get_subagent_prompts_dir(app_home: str | Path | None = None) -> Path:
    """Directory for editable subagent prompt markdown files (mirrors bundled defaults)."""
    return ensure_dir(get_app_home(app_home) / SUBAGENT_PROMPTS_DIRNAME)


def sync_subagent_prompts_from_package(app_home: str | Path | None = None, *, silent: bool = False) -> list[str]:
    """Copy bundled subagent *.md into app_home/subagent_prompts when missing (like default skills)."""
    import opensprite

    home = get_app_home(app_home)
    dest_root = get_subagent_prompts_dir(home)
    bundled_root = Path(opensprite.__file__).resolve().parent / SUBAGENT_PROMPTS_DIRNAME
    if not bundled_root.is_dir():
        return []

    changed: list[str] = []
    for src in sorted(bundled_root.glob("*.md")):
        dest = dest_root / src.name
        if dest.exists():
            continue
        shutil.copy2(src, dest)
        changed.append(_relative_path(dest, home))

    if changed and not silent:
        logger.info("Synced subagent prompt files: %s", changed)

    return changed


def get_workspace_path(workspace: str | Path | None = None) -> Path:
    """Backward-compatible helper for the tool workspace path."""
    if workspace is not None:
        return ensure_dir(Path(workspace).expanduser())
    return get_tool_workspace()


def split_session_chat_id(session_chat_id: str | None) -> tuple[str, str]:
    """Split a session chat id into channel and raw chat id."""
    value = (session_chat_id or "default").strip() or "default"
    if ":" in value:
        channel, chat_id = value.split(":", 1)
        return channel.strip() or "default", chat_id.strip() or "default"
    return "default", value


def _sanitize_path_segment(value: str, default: str = "default", max_length: int = 48) -> str:
    """Sanitize a path segment while keeping collisions unlikely."""
    raw = (value or "").strip() or default
    normalized = re.sub(r"\s+", "-", raw)
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", normalized)
    slug = re.sub(r"-+", "-", slug).strip(" ._-") or default

    needs_hash = slug != raw or len(slug) > max_length
    slug = slug[:max_length].rstrip(" ._-") or default
    if needs_hash:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        slug = f"{slug}-{digest}"[: max_length + 9].rstrip(" ._-")
    return slug or default


def get_chat_workspace(
    chat_id: str | None,
    *,
    workspace_root: str | Path | None = None,
    app_home: str | Path | None = None,
) -> Path:
    """Get the isolated workspace directory for a chat session."""
    root = ensure_dir(Path(workspace_root).expanduser()) if workspace_root is not None else get_tool_workspace(app_home)
    channel, raw_chat_id = split_session_chat_id(chat_id)
    safe_channel = _sanitize_path_segment(channel, default="default", max_length=32)
    safe_chat_id = _sanitize_path_segment(raw_chat_id, default="default")
    return ensure_dir(root / WORKSPACE_CHATS_DIRNAME / safe_channel / safe_chat_id)


def get_chat_skills_dir(
    chat_id: str | None,
    *,
    workspace_root: str | Path | None = None,
    app_home: str | Path | None = None,
) -> Path:
    """Get the personal/per-chat skills directory for a chat session."""
    return get_chat_workspace(chat_id, workspace_root=workspace_root, app_home=app_home) / SKILLS_DIRNAME


def get_memory_file(memory_dir: str | Path, chat_id: str = "default") -> Path:
    """Get the memory file path for a chat without creating it."""
    safe_chat_id = _sanitize_path_segment(chat_id, default="default", max_length=72)
    return Path(memory_dir).expanduser() / safe_chat_id / "MEMORY.md"


def get_recent_summary_file(memory_dir: str | Path, chat_id: str = "default") -> Path:
    """Get the recent summary file path for a chat without creating it."""
    return get_memory_file(memory_dir, chat_id).with_name("RECENT_SUMMARY.md")


def get_recent_summary_state_file(memory_dir: str | Path) -> Path:
    """Get the persisted state file for recent summary updates."""
    return Path(memory_dir).expanduser() / RECENT_SUMMARY_STATE_FILENAME


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _copy_missing_file(source: Path, dest: Path, root: Path) -> str | None:
    """Copy a file if it exists and the destination is missing."""
    if not source.exists() or dest.exists():
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    return _relative_path(dest, root)


def _copy_missing_tree(source: Path, dest: Path, root: Path) -> list[str]:
    """Copy a directory tree without overwriting existing files."""
    copied: list[str] = []

    if not source.exists():
        return copied

    if source.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            copied.extend(_copy_missing_tree(child, dest / child.name, root))
        return copied

    copied_file = _copy_missing_file(source, dest, root)
    if copied_file:
        copied.append(copied_file)
    return copied


def migrate_legacy_bootstrap(app_home: str | Path | None = None, silent: bool = False) -> list[str]:
    """Copy legacy bootstrap markdown files into ~/.opensprite/bootstrap."""
    home = get_app_home(app_home)
    bootstrap_dir = get_bootstrap_dir(home)
    workspace_dir = get_tool_workspace(home)
    migrated: list[str] = []

    for source_dir in (home, workspace_dir):
        for filename in BOOTSTRAP_FILES:
            source = source_dir / filename
            dest = bootstrap_dir / filename
            if source.resolve(strict=False) == dest.resolve(strict=False):
                continue
            copied = _copy_missing_file(source, dest, home)
            if copied:
                migrated.append(copied)

    if migrated and not silent:
        logger.info("Migrated legacy bootstrap files: %s", migrated)

    return migrated


def migrate_legacy_memory(app_home: str | Path | None = None, silent: bool = False) -> list[str]:
    """Copy legacy memory files into ~/.opensprite/memory/{chat_id}/MEMORY.md."""
    home = get_app_home(app_home)
    memory_dir = get_memory_dir(home)
    workspace_memory_dir = get_tool_workspace(home) / MEMORY_DIRNAME
    migrated: list[str] = []

    legacy_default_files = [
        memory_dir / "MEMORY.md",
        workspace_memory_dir / "MEMORY.md",
    ]
    default_memory_file = get_memory_file(memory_dir)
    for source in legacy_default_files:
        copied = _copy_missing_file(source, default_memory_file, home)
        if copied:
            migrated.append(copied)

    if workspace_memory_dir.exists():
        for item in workspace_memory_dir.iterdir():
            if item.name == "MEMORY.md":
                continue
            migrated.extend(_copy_missing_tree(item, memory_dir / item.name, home))

    if migrated and not silent:
        logger.info("Migrated legacy memory files: %s", migrated)

    return migrated


def sync_templates(app_home: str | Path | None = None, silent: bool = False) -> list[str]:
    """Sync bundled templates into ~/.opensprite app directories."""
    home = get_app_home(app_home)
    bootstrap_dir = get_bootstrap_dir(home)
    memory_dir = get_memory_dir(home)
    skills_dir = get_skills_dir(home)
    get_tool_workspace(home)

    changed: list[str] = []
    changed.extend(migrate_legacy_bootstrap(home, silent=True))
    changed.extend(migrate_legacy_memory(home, silent=True))

    try:
        from importlib.resources import files as pkg_files

        templates_root = pkg_files("opensprite") / "templates"
        skills_root = pkg_files("opensprite") / "skills"
    except Exception:
        return changed

    def _write(src, dest: Path, *, overwrite: bool = False) -> None:
        content = src.read_text(encoding="utf-8") if src else ""
        if dest.exists():
            if not overwrite:
                return
            if dest.read_text(encoding="utf-8") == content:
                return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        changed.append(_relative_path(dest, home))

    if templates_root.is_dir():
        for item in templates_root.iterdir():
            if item.name.endswith(".md"):
                _write(item, bootstrap_dir / item.name)

        memory_templates = templates_root / "memory"
        if memory_templates.is_dir():
            default_memory_dir = memory_dir / "default"
            default_memory_dir.mkdir(parents=True, exist_ok=True)
            for item in memory_templates.iterdir():
                if item.name.endswith(".md"):
                    _write(item, default_memory_dir / item.name)

    if skills_root.is_dir():
        skills_dir.mkdir(parents=True, exist_ok=True)
        for skill_folder in skills_root.iterdir():
            if not skill_folder.is_dir():
                continue
            skill_dest = skills_dir / skill_folder.name
            skill_dest.mkdir(parents=True, exist_ok=True)
            for item in skill_folder.iterdir():
                if item.name.endswith((".md", ".py")):
                    _write(item, skill_dest / item.name, overwrite=True)

    changed.extend(sync_subagent_prompts_from_package(home, silent=True))

    if changed and not silent:
        logger.info("Synced template files: %s", changed)

    return changed


def load_bootstrap_files(bootstrap_dir: str | Path) -> dict[str, str]:
    """Load bootstrap markdown files from the bootstrap directory."""
    result = {}
    base_dir = Path(bootstrap_dir).expanduser()
    for filename in BOOTSTRAP_FILES:
        file_path = base_dir / filename
        result[filename.removesuffix(".md")] = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
    return result


def load_memory(memory_dir: str | Path, chat_id: str = "default") -> str:
    """Load long-term memory for a chat."""
    target = get_memory_file(memory_dir, chat_id)
    if target.exists():
        return target.read_text(encoding="utf-8")

    if chat_id == "default":
        legacy_default = Path(memory_dir).expanduser() / "MEMORY.md"
        if legacy_default.exists():
            return legacy_default.read_text(encoding="utf-8")

    return ""


def save_memory(memory_dir: str | Path, content: str, chat_id: str = "default") -> None:
    """Save long-term memory for a chat."""
    memory_path = get_memory_file(memory_dir, chat_id)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(content, encoding="utf-8")
