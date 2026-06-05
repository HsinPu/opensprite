"""Runtime capability catalog used by the task planner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .harness_profile import (
    ANALYSIS_TASK_TYPE,
    GENERIC_TASK_TYPE,
    PLANNING_TASK_TYPE,
    PURE_ANSWER_TASK_TYPE,
)
from .tool_groups import TASK_TYPE_BY_TOOL_GROUP, TOOL_GROUPS
from ..tools.registry import ToolRegistry


_UNGROUPED_TOOL_PREFIX = "tool:"
_MAX_TOOL_DESCRIPTION_CHARS = 220


@dataclass(frozen=True)
class PlannerCapability:
    """One planner-visible capability derived from runtime tools."""

    id: str
    task_type: str
    tools: tuple[str, ...]
    tool_summaries: tuple[dict[str, str], ...] = ()
    risk_levels: tuple[str, ...] = ()

    def to_prompt_metadata(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_type": self.task_type,
            "tools": list(self.tool_summaries) or [{"name": name} for name in self.tools],
            "risk_levels": list(self.risk_levels),
        }


@dataclass(frozen=True)
class PlannerCapabilityCatalog:
    """Planner-facing view of capabilities available in the current runtime."""

    capabilities: tuple[PlannerCapability, ...]

    @property
    def tool_group_ids(self) -> tuple[str, ...]:
        return tuple(capability.id for capability in self.capabilities)

    @property
    def task_types(self) -> tuple[str, ...]:
        values = [PURE_ANSWER_TASK_TYPE, PLANNING_TASK_TYPE, GENERIC_TASK_TYPE, ANALYSIS_TASK_TYPE]
        for capability in self.capabilities:
            if capability.task_type not in values:
                values.append(capability.task_type)
        return tuple(values)

    @property
    def capability_tools(self) -> dict[str, tuple[str, ...]]:
        return {capability.id: capability.tools for capability in self.capabilities}

    def tools_for_group(self, tool_group: str) -> tuple[str, ...]:
        return self.capability_tools.get(str(tool_group or "").strip(), ())

    def to_prompt_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "available_task_types": list(self.task_types),
            "available_capabilities": [capability.to_prompt_metadata() for capability in self.capabilities],
        }


def build_planner_capability_catalog(tool_registry: ToolRegistry | None = None) -> PlannerCapabilityCatalog:
    """Build a planner capability catalog from current runtime tools."""
    if tool_registry is None:
        return _catalog_from_static_tool_groups()
    available_tools = _available_tools(tool_registry)
    group_to_tools: dict[str, list[Any]] = {group: [] for group in TOOL_GROUPS}
    dynamic_group_order: list[str] = []
    for tool in available_tools:
        groups = set(_known_groups_for_tool(tool.name))
        groups.update(_declared_capability_groups(tool))
        if not groups:
            groups.add(f"{_UNGROUPED_TOOL_PREFIX}{tool.name}")
        for group in groups:
            if group not in group_to_tools:
                group_to_tools[group] = []
                dynamic_group_order.append(group)
            group_to_tools[group].append(tool)

    capabilities: list[PlannerCapability] = []
    for group in (*TOOL_GROUPS.keys(), *dynamic_group_order):
        tools = group_to_tools.get(group) or []
        if not tools:
            continue
        capabilities.append(_capability_from_tools(group, tools))
    return PlannerCapabilityCatalog(tuple(capabilities))


def _catalog_from_static_tool_groups() -> PlannerCapabilityCatalog:
    capabilities = [
        PlannerCapability(
            id=group,
            task_type=TASK_TYPE_BY_TOOL_GROUP.get(group, GENERIC_TASK_TYPE),
            tools=tuple(sorted(tools)),
            tool_summaries=tuple({"name": name} for name in sorted(tools)),
        )
        for group, tools in TOOL_GROUPS.items()
    ]
    return PlannerCapabilityCatalog(tuple(capabilities))


def _available_tools(tool_registry: ToolRegistry) -> list[Any]:
    exposed_names = set(tool_registry.tool_names)
    return [
        tool
        for tool in tool_registry.registered_tools()
        if tool.name in exposed_names
    ]


def _known_groups_for_tool(tool_name: str) -> tuple[str, ...]:
    return tuple(
        group
        for group, tool_names in TOOL_GROUPS.items()
        if tool_name in tool_names
    )


def _declared_capability_groups(tool: Any) -> tuple[str, ...]:
    raw = getattr(tool, "capability_groups", None)
    if callable(raw):
        raw = raw()
    if raw is None:
        return ()
    groups: list[str] = []
    for item in raw:
        group = str(item or "").strip()
        if group and group not in groups:
            groups.append(group)
    return tuple(groups)


def _capability_from_tools(group: str, tools: list[Any]) -> PlannerCapability:
    tool_names = tuple(dict.fromkeys(tool.name for tool in tools))
    risk_levels = sorted({
        risk
        for tool in tools
        for risk in (tool.risk_levels or ())
    })
    return PlannerCapability(
        id=group,
        task_type=TASK_TYPE_BY_TOOL_GROUP.get(group, GENERIC_TASK_TYPE),
        tools=tool_names,
        tool_summaries=tuple(_tool_summary(tool) for tool in tools),
        risk_levels=tuple(risk_levels),
    )


def _tool_summary(tool: Any) -> dict[str, str]:
    description = str(getattr(tool, "description", "") or "").strip()
    summary = {
        "name": str(getattr(tool, "name", "") or ""),
    }
    if description:
        summary["description"] = _truncate(description, _MAX_TOOL_DESCRIPTION_CHARS)
    return summary


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 3)].rstrip() + "..."
