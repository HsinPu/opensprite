"""Shared subagent tool-profile metadata helpers."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

from ..subagent_prompts import load_metadata
from ..tool_names import (
    APPLY_PATCH_TOOL_NAME,
    BATCH_TOOL_NAME,
    EDIT_FILE_TOOL_NAME,
    EXECUTION_TOOL_NAMES,
    GLOB_FILES_TOOL_NAME,
    GREP_FILES_TOOL_NAME,
    LIST_DIR_TOOL_NAME,
    READ_FILE_TOOL_NAME,
    READ_SKILL_TOOL_NAME,
    WORKSPACE_WRITE_TOOL_NAMES,
    WRITE_FILE_TOOL_NAME,
)
from ..tools.permissions import PermissionDecision, ToolPermissionPolicy
from ..tools.registry import ToolRegistry
from .retrieval import HISTORY_SEARCH_TOOL_NAME
from .tool_access import ToolAccessResolver
from ..tools.evidence import WEB_SOURCE_EVIDENCE_TOOLS


TOOL_PROFILE_METADATA_FIELD = "tool_profile"
TOOL_PROFILE_NAMES = frozenset({"read-only", "research", "implementation", "testing"})


def normalize_metadata_value(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1].strip()
    return text


def allowed_tool_profile_names() -> list[str]:
    """Return supported frontmatter tool profile names."""
    return sorted(TOOL_PROFILE_NAMES)


def validate_tool_profile_name(tool_profile: Any) -> str | None:
    """Return an error when a tool_profile value is not supported."""
    normalized = normalize_metadata_value(tool_profile)
    if normalized in TOOL_PROFILE_NAMES:
        return None
    allowed = ", ".join(allowed_tool_profile_names())
    return f"tool_profile must be one of: {allowed}."


READ_ONLY_TOOLS = frozenset(
    {
        READ_FILE_TOOL_NAME,
        LIST_DIR_TOOL_NAME,
        GLOB_FILES_TOOL_NAME,
        GREP_FILES_TOOL_NAME,
        BATCH_TOOL_NAME,
        READ_SKILL_TOOL_NAME,
        HISTORY_SEARCH_TOOL_NAME,
    }
)
WEB_TOOLS = WEB_SOURCE_EVIDENCE_TOOLS
WRITE_TOOLS = WORKSPACE_WRITE_TOOL_NAMES
EXEC_TOOLS = EXECUTION_TOOL_NAMES

TEST_WRITE_PATTERNS = frozenset(
    {
        "test/**",
        "tests/**",
        "**/test/**",
        "**/tests/**",
        "__tests__/**",
        "**/__tests__/**",
        "**/test_*.py",
        "**/*_test.py",
        "test_*.py",
        "*_test.py",
        "**/*.test.*",
        "**/*.spec.*",
        "*.test.*",
        "*.spec.*",
    }
)


@dataclass(frozen=True)
class SubagentToolProfile:
    """Allowed runtime tools for one class of subagent."""

    name: str
    allowed_tools: frozenset[str]
    write_path_patterns: frozenset[str] = frozenset()


READ_ONLY_PROFILE = SubagentToolProfile("read-only", READ_ONLY_TOOLS)
RESEARCH_PROFILE = SubagentToolProfile("research", READ_ONLY_TOOLS | WEB_TOOLS)
IMPLEMENTATION_PROFILE = SubagentToolProfile(
    "implementation",
    READ_ONLY_TOOLS | WRITE_TOOLS | EXEC_TOOLS,
)
TESTING_PROFILE = SubagentToolProfile(
    "testing",
    READ_ONLY_TOOLS | WRITE_TOOLS | EXEC_TOOLS,
    write_path_patterns=TEST_WRITE_PATTERNS,
)

TOOL_PROFILES_BY_NAME: dict[str, SubagentToolProfile] = {
    READ_ONLY_PROFILE.name: READ_ONLY_PROFILE,
    RESEARCH_PROFILE.name: RESEARCH_PROFILE,
    IMPLEMENTATION_PROFILE.name: IMPLEMENTATION_PROFILE,
    TESTING_PROFILE.name: TESTING_PROFILE,
}

PARALLEL_SAFE_PROFILE_NAMES = frozenset({READ_ONLY_PROFILE.name, RESEARCH_PROFILE.name})

CODE_REVIEWER_PROMPT_TYPE = "code-reviewer"
SECURITY_REVIEWER_PROMPT_TYPE = "security-reviewer"
ASYNC_CONCURRENCY_REVIEWER_PROMPT_TYPE = "async-concurrency-reviewer"
REVIEW_PROMPT_TYPES = frozenset(
    {
        CODE_REVIEWER_PROMPT_TYPE,
        SECURITY_REVIEWER_PROMPT_TYPE,
        ASYNC_CONCURRENCY_REVIEWER_PROMPT_TYPE,
    }
)

SUBAGENT_TOOL_PROFILES: dict[str, SubagentToolProfile] = {
    "implementer": IMPLEMENTATION_PROFILE,
    "debugger": IMPLEMENTATION_PROFILE,
    "bug-fixer": IMPLEMENTATION_PROFILE,
    "refactorer": IMPLEMENTATION_PROFILE,
    "integration-engineer": IMPLEMENTATION_PROFILE,
    "migration-writer": IMPLEMENTATION_PROFILE,
    "performance-optimizer": IMPLEMENTATION_PROFILE,
    "observability-engineer": IMPLEMENTATION_PROFILE,
    "test-writer": TESTING_PROFILE,
    "test-implementer": TESTING_PROFILE,
    CODE_REVIEWER_PROMPT_TYPE: READ_ONLY_PROFILE,
    SECURITY_REVIEWER_PROMPT_TYPE: READ_ONLY_PROFILE,
    ASYNC_CONCURRENCY_REVIEWER_PROMPT_TYPE: READ_ONLY_PROFILE,
    "pattern-matcher": READ_ONLY_PROFILE,
    "porting-planner": READ_ONLY_PROFILE,
    "api-designer": READ_ONLY_PROFILE,
    "outliner": READ_ONLY_PROFILE,
    "editor": READ_ONLY_PROFILE,
    "writer": RESEARCH_PROFILE,
    "researcher": RESEARCH_PROFILE,
    "fact-checker": RESEARCH_PROFILE,
    "reference-analyzer": RESEARCH_PROFILE,
}


def profile_for_subagent(
    prompt_type: str,
    *,
    app_home: Any = None,
    session_workspace: Any = None,
) -> SubagentToolProfile:
    """Return the runtime tool profile for a subagent id."""
    metadata = load_metadata(
        prompt_type,
        app_home=app_home,
        session_workspace=session_workspace,
    )
    metadata_profile = normalize_metadata_value(metadata.get(TOOL_PROFILE_METADATA_FIELD))
    if metadata_profile:
        profile = TOOL_PROFILES_BY_NAME.get(metadata_profile)
        if profile is None:
            allowed = ", ".join(allowed_tool_profile_names())
            raise ValueError(
                f"subagent '{prompt_type}' has invalid tool_profile '{metadata_profile}'. Allowed: {allowed}"
            )
        return profile
    return SUBAGENT_TOOL_PROFILES.get(prompt_type, READ_ONLY_PROFILE)


class WritePathPermissionPolicy(ToolPermissionPolicy):
    """Restrict filesystem write tools to an allowlist of workspace-relative paths."""

    def __init__(self, allowed_patterns: frozenset[str]):
        self.allowed_patterns = allowed_patterns

    def to_metadata(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot of the write path guardrail."""
        return {
            "kind": "subagent_write_path",
            "allowed_patterns": sorted(self.allowed_patterns),
        }

    def is_tool_exposed(self, tool_name: str, tool_risk_levels: Any = None) -> bool:
        del tool_name, tool_risk_levels
        return True

    @staticmethod
    def _normalize_path(value: Any) -> str:
        return str(value or "").replace("\\", "/").lstrip("./")

    def _path_allowed(self, path: str) -> bool:
        normalized = self._normalize_path(path)
        if not normalized:
            return False
        return any(fnmatch.fnmatch(normalized, pattern) for pattern in self.allowed_patterns)

    @staticmethod
    def _write_paths(tool_name: str, params: Any) -> list[str]:
        if not isinstance(params, dict):
            return []
        if tool_name in {WRITE_FILE_TOOL_NAME, EDIT_FILE_TOOL_NAME}:
            return [str(params.get("path") or "")]
        if tool_name != APPLY_PATCH_TOOL_NAME:
            return []
        changes = params.get("changes")
        if not isinstance(changes, list):
            return []
        return [str(change.get("path") or "") for change in changes if isinstance(change, dict)]

    def check(self, tool_name: str, params: Any, tool_risk_levels: Any = None) -> PermissionDecision:
        del tool_risk_levels
        if tool_name not in WRITE_TOOLS or not self.allowed_patterns:
            return PermissionDecision(True)
        for path in self._write_paths(tool_name, params):
            if not self._path_allowed(path):
                allowed = ", ".join(sorted(self.allowed_patterns))
                return PermissionDecision(
                    False,
                    f"path '{path}' is outside allowed subagent write paths ({allowed})",
                )
        return PermissionDecision(True)


def build_subagent_tool_registry(
    base_registry: ToolRegistry,
    prompt_type: str,
    *,
    app_home: Any = None,
    session_workspace: Any = None,
) -> ToolRegistry:
    """Return a child registry constrained by the subagent capability profile."""
    profile = profile_for_subagent(
        prompt_type,
        app_home=app_home,
        session_workspace=session_workspace,
    )
    overlay_policy = ToolPermissionPolicy(allowed_tools=sorted(profile.allowed_tools))
    extra_policies: tuple[ToolPermissionPolicy, ...] = (
        (WritePathPermissionPolicy(profile.write_path_patterns),)
        if profile.write_path_patterns
        else ()
    )
    resolution = ToolAccessResolver().resolve_overlay(
        base_registry,
        overlay_policy=overlay_policy,
        include_names=profile.allowed_tools,
        extra_policies=extra_policies,
        metadata_kind=f"subagent:{profile.name}",
    )
    return resolution.registry


def supports_parallel_delegation(
    prompt_type: str,
    *,
    app_home: Any = None,
    session_workspace: Any = None,
) -> bool:
    """Return whether a subagent is safe for bounded parallel delegation."""
    return profile_for_subagent(
        prompt_type,
        app_home=app_home,
        session_workspace=session_workspace,
    ).name in PARALLEL_SAFE_PROFILE_NAMES
