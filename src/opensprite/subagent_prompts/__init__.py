"""Subagent Prompts - 預設的 subagent system prompt 模板"""

from pathlib import Path

# 取得 subagent_prompts 目錄路徑
PROMPTS_DIR = Path(__file__).parent

# 工作區路徑（預設）
# 從 agent 動態取得 workspace
def get_current_workspace():
    import os
    workspace = os.environ.get("OPENSPRITE_WORKSPACE")
    if workspace:
        return workspace
    # fallback
    return str(Path(__file__).parent.parent / "workspace" / "chats" / "telegram" / "441751273")

WORKSPACE = get_current_workspace()


def _parse_frontmatter(content: str) -> dict:
    """解析 YAML frontmatter"""
    metadata = {}
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            yaml_content = parts[1].strip()
            for line in yaml_content.split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.strip()] = value.strip()
    return metadata


def load_metadata(prompt_type: str = "writer") -> dict:
    """載入指定 prompt 類型的 metadata"""
    md_path = PROMPTS_DIR / f"{prompt_type}.md"
    if not md_path.exists():
        return {}
    
    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    return _parse_frontmatter(content)


def load_all_metadata() -> dict:
    """載入所有 subagent 的 metadata，回傳 {name: description}"""
    result = {}
    for md_file in PROMPTS_DIR.glob("*.md"):
        metadata = load_metadata(md_file.stem)
        if "name" in metadata and "description" in metadata:
            result[metadata["name"]] = metadata["description"]
    return result


# 預設載入 writer 的 metadata
METADATA = load_metadata("writer")

# 所有可用的 subagent 類型及其描述
ALL_SUBAGENTS = load_all_metadata()

__all__ = ["METADATA", "WORKSPACE", "load_metadata", "load_all_metadata", "ALL_SUBAGENTS"]