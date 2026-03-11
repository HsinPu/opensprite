"""Skill reading tool."""

from typing import Any

from minibot.skills import SkillsLoader
from minibot.tools.base import Tool


class ReadSkillTool(Tool):
    """Tool to read skill instructions."""

    def __init__(self, skills_loader: SkillsLoader):
        self.skills_loader = skills_loader

    @property
    def name(self) -> str:
        return "read_skill"

    @property
    def description(self) -> str:
        return "Read a skill's instructions. Use this when you need to learn how to use a specific skill."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to read (e.g., 'github', 'weather')"
                }
            },
            "required": ["skill_name"]
        }

    async def execute(self, skill_name: str, **kwargs: Any) -> str:
        # Security: validate skill_name (no path traversal)
        if not skill_name or "/" in skill_name or "\\" in skill_name or "." in skill_name:
            return f"Error: Invalid skill name '{skill_name}'"
        
        # Security: check if skill exists in valid skills list
        if skill_name not in self.skills_loader.get_valid_skill_names():
            return f"Error: Skill '{skill_name}' not found"
        
        content = self.skills_loader.load_skill_content(skill_name)
        if not content:
            return f"Error: Skill '{skill_name}' not found"
        
        return content
