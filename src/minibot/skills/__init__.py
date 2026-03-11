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
    """Load and manage skills from skills directories.
    
    Looks in two places:
    - System default: ~/.minibot/skills/
    - Agent custom: workspace/skills/
    """
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        # System default skills
        self.default_skills_dir = Path.home() / ".minibot" / "skills"
        # Agent custom skills
        self.custom_skills_dir = workspace / "skills"
    
    def _load_skills_from_dir(self, skills_dir: Path) -> list[Skill]:
        """Load skills from a directory."""
        skills = []
        if not skills_dir.exists():
            return skills
        
        for skill_dir in skills_dir.iterdir():
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
    
    def get_skills(self) -> list[Skill]:
        """Get all available skills from both system and custom directories."""
        skills = []
        
        # Load from system default skills (~/.minibot/skills/)
        skills.extend(self._load_skills_from_dir(self.default_skills_dir))
        
        # Load from custom skills (workspace/skills/)
        if self.custom_skills_dir.exists():
            for skill_dir in self.custom_skills_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                # Skip if already loaded from default
                if any(s.name == skill_dir.name for s in skills):
                    continue
                skills.extend(self._load_skills_from_dir(self.custom_skills_dir))
        
        return skills
    
    def get_always_skills(self) -> list[str]:
        """Get names of skills that should always be loaded."""
        return [s.name for s in self.get_skills() if s.always]
    
    def build_skills_summary(self) -> str:
        """Build a summary of all skills for the agent to know what's available."""
        skills = self.get_skills()
        if not skills:
            return ""
        
        lines = ["Available skills (use read_skill tool to read instructions):"]
        for s in skills:
            always = " (always on)" if s.always else ""
            lines.append(f"- {s.name}: {s.description}{always}")
        
        return "\n".join(lines)
    
    def load_skill_content(self, skill_name: str) -> str:
        """Load the full content of a skill's SKILL.md."""
        # Check custom skills first (user-defined takes priority)
        for skills_dir in [self.custom_skills_dir, self.default_skills_dir]:
            if not skills_dir.exists():
                continue
            skill_file = skills_dir / skill_name / "SKILL.md"
            if skill_file.exists():
                content = skill_file.read_text(encoding="utf-8")
                # Skip frontmatter
                lines = content.split("\n")
                start = 0
                for i, line in enumerate(lines):
                    if line == "---":
                        start = i + 1
                        break
                return "\n".join(lines[start:]).strip()
        return ""
    
    def get_skill_path(self, skill_name: str) -> Path | None:
        """Get the file path for a skill's SKILL.md."""
        for skills_dir in [self.custom_skills_dir, self.default_skills_dir]:
            if not skills_dir.exists():
                continue
            skill_file = skills_dir / skill_name / "SKILL.md"
            if skill_file.exists():
                return skill_file.resolve()
        return None
    
    def skill_exists(self, skill_name: str) -> bool:
        """Check if a skill exists."""
        return self.get_skill_path(skill_name) is not None
    
    def get_valid_skill_names(self) -> list[str]:
        """Get list of valid skill names for validation."""
        return [s.name for s in self.get_skills()]
    
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
