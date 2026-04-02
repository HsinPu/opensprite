"""Subagent prompt registry and prompt-loading helpers."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


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


def _get_prompt_path(prompt_type: str) -> Path:
    """Return the markdown file path for a prompt type."""
    return PROMPTS_DIR / f"{prompt_type}.md"


def load_metadata(prompt_type: str = "writer") -> dict:
    """Load frontmatter metadata for a prompt type."""
    md_path = _get_prompt_path(prompt_type)
    if not md_path.exists():
        return {}
    
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    return _parse_frontmatter(content)


def load_prompt(prompt_type: str = "writer") -> str:
    """Load prompt markdown content without frontmatter."""
    md_path = _get_prompt_path(prompt_type)
    if not md_path.exists():
        return ""

    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    _, body = _split_frontmatter(content)
    return body.strip()


def load_all_metadata() -> dict:
    """Load prompt descriptions as {prompt_type: description}."""
    result = {}
    for md_file in PROMPTS_DIR.glob("*.md"):
        metadata = load_metadata(md_file.stem)
        description = metadata.get("description", md_file.stem)
        result[md_file.stem] = description
    return dict(sorted(result.items()))


def get_prompt_types() -> list[str]:
    """Return all available prompt types."""
    return list(load_all_metadata().keys())


def has_prompt(prompt_type: str) -> bool:
    """Check whether a prompt file exists for the given type."""
    return _get_prompt_path(prompt_type).exists()


# Available subagent prompt types and descriptions.
ALL_SUBAGENTS = load_all_metadata()

__all__ = [
    "load_metadata",
    "load_prompt",
    "load_all_metadata",
    "get_prompt_types",
    "has_prompt",
    "ALL_SUBAGENTS",
]
