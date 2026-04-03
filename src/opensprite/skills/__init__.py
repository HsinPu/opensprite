"""Skills system for OpenSprite."""

from pathlib import Path
from typing import Any


class Skill:
    """A skill that extends agent capabilities."""

    def __init__(self, name: str, description: str, always: bool = False):
        self.name = name
        self.description = description
        self.always = always


class SkillsLoader:
    """Load skills from default and optional workspace directories.

    Looks in two places:
    - Default user skills: ~/.opensprite/skills/
    - Optional workspace override: <workspace>/skills/
    """

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        default_skills_dir: Path | None = None,
        custom_skills_dir: Path | None = None,
    ):
        self.workspace = Path(workspace).expanduser() if workspace else None
        self.default_skills_dir = (
            Path(default_skills_dir).expanduser()
            if default_skills_dir is not None
            else Path.home() / ".opensprite" / "skills"
        )
        if custom_skills_dir is not None:
            self.custom_skills_dir = Path(custom_skills_dir).expanduser()
        elif self.workspace is not None:
            self.custom_skills_dir = self.workspace / "skills"
        else:
            self.custom_skills_dir = None

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

            try:
                content = skill_file.read_text(encoding="utf-8")
                frontmatter = self._parse_frontmatter(content)
                skills.append(
                    Skill(
                        name=frontmatter.get("name", skill_dir.name),
                        description=frontmatter.get("description", ""),
                        always=frontmatter.get("always", False),
                    )
                )
            except Exception:
                continue

        return skills

    def _split_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        """Split YAML frontmatter from the markdown body."""
        lines = content.split("\n")
        if len(lines) < 3 or lines[0] != "---":
            return {}, content

        frontmatter: dict[str, Any] = {}
        end_index = 0
        for index, line in enumerate(lines[1:], start=1):
            if line == "---":
                end_index = index
                break
            if ":" not in line:
                continue

            key, value = line.split(":", 1)
            parsed_value: Any = value.strip()
            if parsed_value == "true":
                parsed_value = True
            elif parsed_value == "false":
                parsed_value = False
            frontmatter[key.strip()] = parsed_value

        if end_index == 0:
            return {}, content

        body = "\n".join(lines[end_index + 1 :]).strip()
        return frontmatter, body

    def _iter_skill_dirs(self) -> list[Path]:
        """Return unique skill directories in priority order."""
        candidates = [self.custom_skills_dir, self.default_skills_dir]
        dirs: list[Path] = []
        seen: set[Path] = set()

        for candidate in candidates:
            if candidate is None or not candidate.exists():
                continue

            resolved = candidate.resolve()
            if resolved in seen:
                continue

            seen.add(resolved)
            dirs.append(candidate)

        return dirs

    def get_skills(self) -> list[Skill]:
        """Get all available skills from custom then default directories."""
        skills: list[Skill] = []
        seen_names: set[str] = set()

        for skills_dir in self._iter_skill_dirs():
            for skill in self._load_skills_from_dir(skills_dir):
                if skill.name in seen_names:
                    continue
                seen_names.add(skill.name)
                skills.append(skill)

        return skills

    def get_loaded_skills(self) -> list[str]:
        """Get names of skills that are loaded into the main system prompt."""
        return [skill.name for skill in self.get_skills()]

    def get_always_skills(self) -> list[str]:
        """Backward-compatible alias for skills loaded into the system prompt."""
        return self.get_loaded_skills()

    def build_skills_summary(self) -> str:
        """Build a summary of available skills."""
        skills = self.get_skills()
        if not skills:
            return ""

        lines = ["Available skills (use read_skill tool to read instructions):"]
        for skill in skills:
            lines.append(f"- {skill.name}: {skill.description}")

        return "\n".join(lines)

    def load_skill_content(self, skill_name: str) -> str:
        """Load the full content of a skill's SKILL.md."""
        for skills_dir in self._iter_skill_dirs():
            skill_file = skills_dir / skill_name / "SKILL.md"
            if not skill_file.exists():
                continue

            content = skill_file.read_text(encoding="utf-8")
            _, body = self._split_frontmatter(content)
            return body

        return ""

    def get_skill_path(self, skill_name: str) -> Path | None:
        """Get the file path for a skill's SKILL.md."""
        for skills_dir in self._iter_skill_dirs():
            skill_file = skills_dir / skill_name / "SKILL.md"
            if skill_file.exists():
                return skill_file.resolve()
        return None

    def skill_exists(self, skill_name: str) -> bool:
        """Check if a skill exists."""
        return self.get_skill_path(skill_name) is not None

    def get_valid_skill_names(self) -> list[str]:
        """Get valid skill names for validation."""
        return [skill.name for skill in self.get_skills()]

    def _parse_frontmatter(self, content: str) -> dict[str, Any]:
        """Parse YAML frontmatter from SKILL.md."""
        frontmatter, _ = self._split_frontmatter(content)
        return frontmatter
