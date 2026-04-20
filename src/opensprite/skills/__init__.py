"""Skills system for OpenSprite."""

from pathlib import Path
from typing import Any


class Skill:
    """A skill that extends agent capabilities."""

    def __init__(self, name: str, description: str, path: Path):
        self.name = name
        self.description = description
        self.path = path


class SkillsLoader:
    """Load skills from exactly two places:

    1. The current **session workspace** ``.../skills/<skill_id>/SKILL.md`` (mutable; same
       tree the filesystem tools use for this chat).
    2. **Bundled** skills under ``~/.opensprite/skills/<skill_id>/SKILL.md`` (read-only for agents).

    When the same skill id exists in both, the session workspace copy wins.

    Skills are intended for on-demand loading: the agent should first see the
    available skill names and descriptions, then load a full SKILL.md only when
    the task clearly matches that skill.
    """

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        skills_root: Path | None = None,
        default_skills_dir: Path | None = None,
        personal_skills_dir: Path | None = None,
        custom_skills_dir: Path | None = None,
    ):
        self.workspace = Path(workspace).expanduser() if workspace else None
        root = skills_root if skills_root is not None else default_skills_dir
        self.skills_root = (
            Path(root).expanduser()
            if root is not None
            else Path.home() / ".opensprite" / "skills"
        )
        personal_dir = personal_skills_dir if personal_skills_dir is not None else custom_skills_dir
        self.personal_skills_dir = Path(personal_dir).expanduser() if personal_dir is not None else None

    @property
    def default_skills_dir(self) -> Path:
        """Backward-compatible alias: the app-home skills root (``~/.opensprite/skills``)."""
        return self.skills_root

    def _resolve_personal_skills_dir(self, personal_skills_dir: Path | None = None) -> Path | None:
        """Resolve the session workspace ``skills/`` directory for this request."""
        if personal_skills_dir is not None:
            return Path(personal_skills_dir).expanduser()
        return self.personal_skills_dir

    def _load_skills_from_dir(self, skills_dir: Path) -> list[Skill]:
        """Load skills from a directory (each immediate child with ``SKILL.md``)."""
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
                        path=skill_file.resolve(),
                    )
                )
            except Exception:
                continue

        return skills

    def _split_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        """Split YAML frontmatter from the markdown body."""
        lines = content.splitlines()
        if len(lines) < 3 or lines[0].strip() != "---":
            return {}, content

        frontmatter: dict[str, Any] = {}
        end_index = 0
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
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

    def _iter_skill_dirs(self, personal_skills_dir: Path | None = None) -> list[Path]:
        """Session ``skills/`` first, then app-home ``~/.opensprite/skills/``."""
        dirs: list[Path] = []
        seen: set[Path] = set()

        def add(candidate: Path | None) -> None:
            if candidate is None:
                return
            if not candidate.is_dir():
                return
            resolved = candidate.resolve()
            if resolved in seen:
                return
            seen.add(resolved)
            dirs.append(resolved)

        add(self._resolve_personal_skills_dir(personal_skills_dir))
        add(self.skills_root)

        return dirs

    def get_skills(self, personal_skills_dir: Path | None = None) -> list[Skill]:
        """Session workspace skills override bundled app-home skills for duplicate names."""
        skills: list[Skill] = []
        seen_names: set[str] = set()

        for skills_dir in self._iter_skill_dirs(personal_skills_dir):
            for skill in self._load_skills_from_dir(skills_dir):
                if skill.name in seen_names:
                    continue
                seen_names.add(skill.name)
                skills.append(skill)

        return skills

    def build_skills_summary(self, personal_skills_dir: Path | None = None) -> str:
        """Build lightweight skill metadata for the main system prompt.

        Only skill names and descriptions are included here. Full skill content
        should be loaded later via the read_skill tool when needed.
        """
        skills = self.get_skills(personal_skills_dir)
        if not skills:
            return ""

        lines = ["Available skills (use read_skill tool to read instructions):"]
        for skill in skills:
            lines.append(f"- {skill.name}: {skill.description}")

        return "\n".join(lines)

    def _get_skill(self, skill_name: str, personal_skills_dir: Path | None = None) -> Skill | None:
        """Return the discovered skill metadata for a skill name."""
        for skill in self.get_skills(personal_skills_dir):
            if skill.name == skill_name:
                return skill
        return None

    def load_skill_content(self, skill_name: str, personal_skills_dir: Path | None = None) -> str:
        """Load the full content of a skill's SKILL.md."""
        skill = self._get_skill(skill_name, personal_skills_dir)
        if skill is None:
            return ""

        content = skill.path.read_text(encoding="utf-8")
        _, body = self._split_frontmatter(content)
        return body

    def get_skill_path(self, skill_name: str, personal_skills_dir: Path | None = None) -> Path | None:
        """Get the file path for a skill's SKILL.md."""
        skill = self._get_skill(skill_name, personal_skills_dir)
        return skill.path if skill is not None else None

    def skill_exists(self, skill_name: str, personal_skills_dir: Path | None = None) -> bool:
        """Check if a skill exists."""
        return self._get_skill(skill_name, personal_skills_dir) is not None

    def get_valid_skill_names(self, personal_skills_dir: Path | None = None) -> list[str]:
        """Get valid skill names for validation."""
        return [skill.name for skill in self.get_skills(personal_skills_dir)]

    def _parse_frontmatter(self, content: str) -> dict[str, Any]:
        """Parse YAML frontmatter from SKILL.md."""
        frontmatter, _ = self._split_frontmatter(content)
        return frontmatter
