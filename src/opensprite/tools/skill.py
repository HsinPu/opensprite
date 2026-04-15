"""Skill reading tool."""

from pathlib import Path
from typing import Callable
from typing import Any

from ..skills import SkillsLoader
from .base import Tool
from .validation import NON_EMPTY_STRING_PATTERN


class ReadSkillTool(Tool):
    """Tool to read skill instructions."""

    def __init__(
        self,
        skills_loader: SkillsLoader,
        *,
        personal_skills_dir_resolver: Callable[[], Path | None] | None = None,
    ):
        self.skills_loader = skills_loader
        self._personal_skills_dir_resolver = personal_skills_dir_resolver

    def _get_personal_skills_dir(self) -> Path | None:
        if self._personal_skills_dir_resolver is None:
            return None
        return self._personal_skills_dir_resolver()

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
                    "description": "Name of the skill to read (e.g., 'github', 'weather')",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                }
            },
            "required": ["skill_name"]
        }

    async def _execute(self, skill_name: str, **kwargs: Any) -> str:
        personal_skills_dir = self._get_personal_skills_dir()

        # Security: validate skill_name (no path traversal)
        if "/" in skill_name or "\\" in skill_name or "." in skill_name:
            return f"Error: Invalid skill name '{skill_name}'"
        
        # Security: check if skill exists in valid skills list
        if skill_name not in self.skills_loader.get_valid_skill_names(personal_skills_dir):
            return f"Error: Skill '{skill_name}' not found"
        
        content = self.skills_loader.load_skill_content(skill_name, personal_skills_dir)
        if not content:
            return f"Error: Skill '{skill_name}' not found"
        
        return content
