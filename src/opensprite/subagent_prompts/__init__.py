"""Subagent prompt registry and prompt-loading helpers."""

from pathlib import Path

from ..context.paths import get_app_home, get_subagent_prompts_dir, sync_subagent_prompts_from_package

# Bundled package directory (fallback if user removed a file from app home)
BUNDLED_PROMPTS_DIR = Path(__file__).parent


def _split_frontmatter(content: str) -> tuple[dict, str]:
    """Split YAML frontmatter and return metadata plus markdown body."""
    metadata = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            yaml_content = parts[1].strip()
            body = parts[2].lstrip()
            for line in yaml_content.split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.strip()] = value.strip()
    return metadata, body


def _parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter metadata from a prompt file."""
    metadata, _ = _split_frontmatter(content)
    return metadata


def _get_prompt_path(prompt_type: str, app_home: Path | None) -> Path:
    """Resolve the markdown path for a prompt type (user app home first, else bundled)."""
    home = get_app_home(app_home)
    sync_subagent_prompts_from_package(home)
    user_path = get_subagent_prompts_dir(home) / f"{prompt_type}.md"
    if user_path.exists():
        return user_path
    bundled = BUNDLED_PROMPTS_DIR / f"{prompt_type}.md"
    return bundled if bundled.exists() else user_path


def load_metadata(prompt_type: str = "writer", *, app_home: Path | None = None) -> dict:
    """Load frontmatter metadata for a prompt type."""
    md_path = _get_prompt_path(prompt_type, app_home)
    if not md_path.exists():
        return {}

    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    return _parse_frontmatter(content)


def load_prompt(prompt_type: str = "writer", *, app_home: Path | None = None) -> str:
    """Load prompt markdown content without frontmatter."""
    md_path = _get_prompt_path(prompt_type, app_home)
    if not md_path.exists():
        return ""

    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    _, body = _split_frontmatter(content)
    return body.strip()


def load_all_metadata(*, app_home: Path | None = None) -> dict[str, str]:
    """Load prompt descriptions as {prompt_type: description} from ~/.opensprite/subagent_prompts."""
    home = get_app_home(app_home)
    sync_subagent_prompts_from_package(home)
    user_dir = get_subagent_prompts_dir(home)
    result: dict[str, str] = {}
    for md_file in sorted(user_dir.glob("*.md")):
        metadata = load_metadata(md_file.stem, app_home=home)
        description = metadata.get("description", md_file.stem)
        result[md_file.stem] = description
    return dict(sorted(result.items()))


def get_all_subagents(app_home: Path | None = None) -> dict[str, str]:
    """Return available subagent types and short descriptions (same as load_all_metadata)."""
    return load_all_metadata(app_home=app_home)


def get_prompt_types(app_home: Path | None = None) -> list[str]:
    """Return all available prompt types."""
    return list(load_all_metadata(app_home=app_home).keys())


def has_prompt(prompt_type: str, *, app_home: Path | None = None) -> bool:
    """Check whether a prompt file exists for the given type."""
    return _get_prompt_path(prompt_type, app_home).exists()


def __getattr__(name: str):
    if name == "ALL_SUBAGENTS":
        return load_all_metadata()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BUNDLED_PROMPTS_DIR",
    "load_metadata",
    "load_prompt",
    "load_all_metadata",
    "get_all_subagents",
    "get_prompt_types",
    "has_prompt",
    "ALL_SUBAGENTS",
]
