"""Skills system for mini-bot."""

from pathlib import Path
from typing import Any


class Skill:
    """A skill that extends agent capabilities."""
    
    def __init__(self, name: str, description: str, always: bool = False):
        self.name = name
        self.description = description
        self.always = always


class SkillsLoader:
    """Load and manage skills from the skills directory."""
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.skills_dir = workspace / "skills"
    
    def get_skills(self) -> list[Skill]:
        """Get all available skills."""
        skills = []
        
        if not self.skills_dir.exists():
            return skills
        
        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            
            # Parse SKILL.md frontmatter
            try:
                content = skill_file.read_text(encoding="utf-8")
                frontmatter = self._parse_frontmatter(content)
                
                skill = Skill(
                    name=frontmatter.get("name", skill_dir.name),
                    description=frontmatter.get("description", ""),
                    always=frontmatter.get("always", False),
                )
                skills.append(skill)
            except Exception:
                continue
        
        return skills
    
    def get_always_skills(self) -> list[str]:
        """Get names of skills that should always be loaded."""
        return [s.name for s in self.get_skills() if s.always]
    
    def load_skill_content(self, skill_name: str) -> str:
        """Load the full content of a skill's SKILL.md."""
        skill_file = self.skills_dir / skill_name / "SKILL.md"
        if not skill_file.exists():
            return ""
        
        content = skill_file.read_text(encoding="utf-8")
        # Skip frontmatter
        lines = content.split("\n")
        start = 0
        for i, line in enumerate(lines):
            if line == "---":
                start = i + 1
                break
        
        return "\n".join(lines[start:]).strip()
    
    def _parse_frontmatter(self, content: str) -> dict[str, Any]:
        """Parse YAML frontmatter from SKILL.md."""
        lines = content.split("\n")
        if len(lines) < 3 or lines[0] != "---":
            return {}
        
        frontmatter = {}
        in_frontmatter = False
        for line in lines[1:]:
            if line == "---":
                break
            if ":" in line:
                key, value = line.split(":", 1)
                value = value.strip()
                # Handle booleans
                if value == "true":
                    value = True
                elif value == "false":
                    value = False
                frontmatter[key.strip()] = value
        
        return frontmatter
