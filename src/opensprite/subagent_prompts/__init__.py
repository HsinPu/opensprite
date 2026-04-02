"""Subagent prompt registry and loaders."""

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
    """解析 YAML frontmatter"""
    metadata, _ = _split_frontmatter(content)
    return metadata


def load_metadata(prompt_type: str = "writer") -> dict:
    """載入指定 prompt 類型的 metadata"""
    md_path = PROMPTS_DIR / f"{prompt_type}.md"
    if not md_path.exists():
        return {}
    
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    return _parse_frontmatter(content)


def load_prompt(prompt_type: str = "writer") -> str:
    """載入指定 prompt 類型的 markdown 內容（不含 frontmatter）"""
    md_path = PROMPTS_DIR / f"{prompt_type}.md"
    if not md_path.exists():
        return ""

    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    _, body = _split_frontmatter(content)
    return body.strip()


def load_all_metadata() -> dict:
    """載入所有 subagent 的 metadata，回傳 {prompt_type: description}"""
    result = {}
    for md_file in PROMPTS_DIR.glob("*.md"):
        metadata = load_metadata(md_file.stem)
        description = metadata.get("description", md_file.stem)
        result[md_file.stem] = description
    return result


# 所有可用的 subagent 類型及其描述
ALL_SUBAGENTS = load_all_metadata()

__all__ = ["load_metadata", "load_prompt", "load_all_metadata", "ALL_SUBAGENTS"]
