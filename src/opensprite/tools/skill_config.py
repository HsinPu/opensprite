"""Tool for safely creating and managing skills (SKILL.md) in dedicated skill directories."""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from ..skills import SkillsLoader
from .base import Tool

WorkspaceResolver = Callable[[], Path]

# Bundled guide skill (see src/opensprite/skills/skill-creator-design/SKILL.md) — full rules for new skills.
SKILL_CREATION_GUIDE_NAME = "skill-creator-design"


def path_touches_read_only_app_skills_dir(file_path: Path) -> str | None:
    """Block writes anywhere under ``~/.opensprite/skills/`` (bundled skills, read-only for agents)."""
    try:
        resolved = file_path.resolve(strict=False)
        from ..context.paths import get_skills_dir

        skills_home = get_skills_dir().resolve(strict=False)
    except OSError:
        return None
    try:
        resolved.relative_to(skills_home)
    except ValueError:
        return None
    return (
        "Error: Cannot modify files under ~/.opensprite/skills/ via write_file or edit_file. "
        "Bundled skills there are read-only; use the session workspace skills/ folder or configure_skill."
    )


# Fixed rules (enforced on disk writes and on any action that takes skill_name).
MIN_SKILL_DESCRIPTION_LEN = 80
# English word tokens (Latin letters); blocks padding with non-words or very short blurbs.
MIN_SKILL_DESCRIPTION_WORDS = 16
# Substantive words after removing common glue words (still English-focused).
MIN_SKILL_DESCRIPTION_CONTENT_WORDS = 12
# If one non-stopword token is more than this fraction of content tokens, treat as low-quality / padding.
MAX_SKILL_DESCRIPTION_TOKEN_DOMINANCE = 0.38
MIN_SKILL_BODY_LEN = 40
MAX_SKILL_ID_LEN = 64

# Stopwords for "substance" checks (not for word-count totals).
_DESCRIPTION_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "nor",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "as",
        "if",
        "so",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
    }
)
# Lowercase, digit segments, hyphen separators; must start with a letter (aligned with skill-creator-design checklist).
_SKILL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")

_CONFIGURE_SKILL_RULES_SUMMARY = (
    "Skill layout: one directory per skill named like the skill id, containing SKILL.md. "
    "Mutable skills live only under the current session workspace skills/; "
    "Bundled skills stay under ~/.opensprite/skills/<id>/ (read-only). "
    "YAML frontmatter must include name (same as skill_name / folder) and description. "
    "Before writing a new skill, read the bundled guide with read_skill using skill_name "
    f"'{SKILL_CREATION_GUIDE_NAME}': it defines concise metadata, English frontmatter, "
    "detailed description (what the skill does + when to trigger), imperative body text, "
    "progressive disclosure, and optional scripts/, references/, assets/ next to SKILL.md."
)


def _validate_skill_id(skill_name: str) -> str | None:
    """Validate skill id for configure_skill (strict, fixed rules)."""
    name = str(skill_name or "").strip()
    if not name:
        return "Error: skill_name is required"
    if "/" in name or "\\" in name or "." in name or ".." in name:
        return f"Error: Invalid skill name '{skill_name}'"
    if len(name) < 2:
        return "Error: skill_name must be at least 2 characters"
    if len(name) > MAX_SKILL_ID_LEN:
        return f"Error: skill_name must be at most {MAX_SKILL_ID_LEN} characters"
    if not _SKILL_ID_PATTERN.match(name):
        return (
            "Error: skill_name must be lowercase ASCII, start with a letter, use hyphens between segments only "
            "(e.g. my-feature, skill-creator-design). Underscores, uppercase, and dots are not allowed."
        )
    return None


def _latin_letter_words(text: str) -> list[str]:
    """Extract English-ish word tokens for description quality checks."""
    return re.findall(r"[A-Za-z][A-Za-z0-9']*", text)


def _validate_description_for_write(description: str | None, *, action: str) -> str | None:
    if description is None:
        return f"Error: description is required for {action}"
    text = str(description).strip()
    if not text:
        return f"Error: description is required for {action}"
    if len(text) < MIN_SKILL_DESCRIPTION_LEN:
        return (
            f"Error: description must be at least {MIN_SKILL_DESCRIPTION_LEN} characters "
            f"(after trim); got {len(text)}. Write a detailed English description (what the skill does and when to use it)."
        )

    words = [w.lower() for w in _latin_letter_words(text)]
    if len(words) < MIN_SKILL_DESCRIPTION_WORDS:
        return (
            f"Error: description must contain at least {MIN_SKILL_DESCRIPTION_WORDS} English words "
            f"(Latin letters); got {len(words)}. Add more detail: what the skill does, when to load it, and typical tasks."
        )

    content_tokens = [w for w in words if w not in _DESCRIPTION_STOPWORDS and len(w) > 2]
    if len(content_tokens) < MIN_SKILL_DESCRIPTION_CONTENT_WORDS:
        return (
            f"Error: description is not detailed enough: need at least {MIN_SKILL_DESCRIPTION_CONTENT_WORDS} "
            "substantive English words (not only articles/prepositions). Explain capabilities and when the agent should use this skill."
        )

    if content_tokens:
        top_count = Counter(content_tokens).most_common(1)[0][1]
        if top_count / len(content_tokens) > MAX_SKILL_DESCRIPTION_TOKEN_DOMINANCE:
            return (
                "Error: description looks too repetitive or padded (same terms dominate). "
                "Rewrite with varied, specific detail about the skill scope and triggers."
            )

    return None


def _validate_body_for_write(body: str | None, *, action: str) -> str | None:
    if body is None:
        return f"Error: body is required for {action}"
    text = str(body).strip()
    if len(text) < MIN_SKILL_BODY_LEN:
        return (
            f"Error: body must be at least {MIN_SKILL_BODY_LEN} characters (after trim); got {len(text)}. "
            "Add imperative instructions for the skill body."
        )
    return None


def _build_skill_md(skill_name: str, description: str, body: str) -> str:
    desc = (description or "").strip().replace("\n", " ").replace("\r", "")
    body_text = (body or "").strip()
    return f"---\nname: {skill_name}\ndescription: {desc}\n---\n\n{body_text}\n"


class ConfigureSkillTool(Tool):
    """Read and update skill definitions under the session workspace ``skills/`` (not under ~/.opensprite/skills/)."""

    name = "configure_skill"
    description = (
        "Inspect, add, update, or remove skills (each skill is a directory containing SKILL.md). "
        "Use this when the user wants a new skill or to change skill metadata and instructions instead of editing files manually. "
        + _CONFIGURE_SKILL_RULES_SUMMARY
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "add", "upsert", "remove"],
                "description": (
                    "list: enumerate skills; get: read one SKILL.md; "
                    "add: create a new skill only (fails if it already exists); "
                    "upsert: create or replace SKILL.md; remove: delete skill directory. "
                    "All paths are under the session workspace skills/ (never ~/.opensprite/skills/)."
                ),
            },
            "skill_name": {
                "type": "string",
                "description": (
                    "Skill id: must match directory and frontmatter name. "
                    "Required format: lowercase ASCII, start with a letter, hyphens only between segments "
                    f"(2–{MAX_SKILL_ID_LEN} chars). Required for get, add, upsert, and remove."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    f"YAML frontmatter for add and upsert: min {MIN_SKILL_DESCRIPTION_LEN} chars, "
                    f"min {MIN_SKILL_DESCRIPTION_WORDS} English words, min {MIN_SKILL_DESCRIPTION_CONTENT_WORDS} substantive words; "
                    "must not be repetitive padding. Cover what the skill does and when to load it. See "
                    f"'{SKILL_CREATION_GUIDE_NAME}'."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    f"Markdown body for add and upsert (min {MIN_SKILL_BODY_LEN} chars after trim). Imperative instructions; "
                    f"lean body per '{SKILL_CREATION_GUIDE_NAME}', long text in references via write_file."
                ),
            },
        },
        "required": ["action"],
    }

    def __init__(
        self,
        skills_loader: SkillsLoader,
        *,
        workspace_resolver: WorkspaceResolver,
    ):
        self._skills_loader = skills_loader
        self._workspace_resolver = workspace_resolver

    def _session_skills_root(self) -> Path:
        return (Path(self._workspace_resolver()) / "skills").resolve()

    @staticmethod
    def _is_under(root: Path, path: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def _list_payload(self, root: Path) -> dict[str, Any]:
        payload: dict[str, Any] = {"skills_dir": str(root), "skills": {}}
        if not root.exists():
            return payload
        for skill in self._skills_loader._load_skills_from_dir(root):
            payload["skills"][skill.name] = {
                "description": skill.description,
                "path": str(skill.path),
            }
        return payload

    async def _execute(self, action: str, **kwargs: Any) -> str:
        root = self._session_skills_root()

        if action == "list":
            return json.dumps(self._list_payload(root), ensure_ascii=False, indent=2)

        skill_name = str(kwargs.get("skill_name", "") or "").strip()
        err = _validate_skill_id(skill_name)
        if err:
            return err

        skill_dir = (root / skill_name).resolve()
        if not self._is_under(root, skill_dir):
            return "Error: skill path escapes skills root"

        skill_file = skill_dir / "SKILL.md"

        if action == "get":
            if not skill_file.is_file():
                return f"Error: skill '{skill_name}' not found under {root}"
            text = skill_file.read_text(encoding="utf-8")
            payload = {
                "skills_dir": str(root),
                "skill_name": skill_name,
                "path": str(skill_file),
                "content": text,
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        if action == "remove":
            if not skill_dir.is_dir():
                return f"Error: skill directory '{skill_name}' not found under {root}"
            shutil.rmtree(skill_dir)
            return f"Removed skill '{skill_name}' from {root}."

        if action in {"add", "upsert"}:
            description = kwargs.get("description")
            body = kwargs.get("body")

            desc_err = _validate_description_for_write(description, action=action)
            if desc_err:
                return desc_err
            body_err = _validate_body_for_write(body, action=action)
            if body_err:
                return body_err

            existed = skill_file.is_file()
            if action == "add" and existed:
                return (
                    f"Error: skill '{skill_name}' already exists at {skill_file}. "
                    "Use action=upsert to replace it, or remove it first."
                )

            root.mkdir(parents=True, exist_ok=True)
            skill_dir.mkdir(parents=True, exist_ok=True)
            content = _build_skill_md(skill_name, str(description), str(body))
            skill_file.write_text(content, encoding="utf-8")
            guide_hint = (
                f" Next: use read_skill with skill_name '{SKILL_CREATION_GUIDE_NAME}' if you have not applied the full checklist; "
                "add optional scripts/, references/, assets/ beside SKILL.md with write_file as needed."
            )
            if action == "add":
                return f"Added skill '{skill_name}' at {skill_file}.{guide_hint}"
            mode = "Updated" if existed else "Added"
            return f"{mode} skill '{skill_name}' at {skill_file}.{guide_hint}"

        return f"Error: unsupported action '{action}'"
