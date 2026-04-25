"""Runtime tool capability profiles for delegated subagents."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

from ..subagent_profiles import (
    TOOL_PROFILE_METADATA_FIELD,
    allowed_tool_profile_names,
    normalize_metadata_value,
)
from ..subagent_prompts import load_metadata
from ..tools.batch import BatchTool
from ..tools.permissions import (
    CompositeToolPermissionPolicy,
    PermissionDecision,
    ToolPermissionPolicy,
)
from ..tools.registry import ToolRegistry


READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "glob_files",
        "grep_files",
        "batch",
        "read_skill",
        "search_history",
        "search_knowledge",
    }
)
WEB_TOOLS = frozenset({"web_search", "web_fetch"})
WRITE_TOOLS = frozenset({"apply_patch", "write_file", "edit_file"})
EXEC_TOOLS = frozenset({"exec", "process"})

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
    "code-reviewer": READ_ONLY_PROFILE,
    "security-reviewer": READ_ONLY_PROFILE,
    "async-concurrency-reviewer": READ_ONLY_PROFILE,
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

    def is_tool_exposed(self, tool_name: str) -> bool:
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
        if tool_name in {"write_file", "edit_file"}:
            return [str(params.get("path") or "")]
        if tool_name != "apply_patch":
            return []
        changes = params.get("changes")
        if not isinstance(changes, list):
            return []
        return [str(change.get("path") or "") for change in changes if isinstance(change, dict)]

    def check(self, tool_name: str, params: Any) -> PermissionDecision:
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
    policies: list[ToolPermissionPolicy] = [base_registry.permission_policy]
    if profile.write_path_patterns:
        policies.append(WritePathPermissionPolicy(profile.write_path_patterns))

    child_registry = base_registry.filtered(
        include_names=profile.allowed_tools,
        permission_policy=CompositeToolPermissionPolicy(*policies),
    )
    if "batch" in child_registry.tool_names:
        child_registry.register(BatchTool(registry_resolver=lambda: child_registry))
    return child_registry
