"""Controlled harness scenario evaluations.

These cases exercise profile guidance, executable permissions, and approval
metadata without calling an external LLM or live web service.
"""

from __future__ import annotations

from typing import Any

from ..agent.harness_inventory import expected_sensor_ids_for_task_type
from ..agent.harness_policy import HarnessPolicyService
from ..agent.harness_profile import HarnessProfileService
from ..agent.task_contract import EvidenceRequirement, TaskContract
from ..tools.base import Tool
from ..tools.registry import ToolRegistry


class _ScenarioTool(Tool):
    def __init__(self, name: str, risk_levels: set[str]):
        self._name = name
        self._risk_levels = frozenset(risk_levels)

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Controlled scenario tool {self._name}"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def risk_levels(self) -> frozenset[str]:
        return self._risk_levels

    async def _execute(self, **kwargs: Any) -> str:
        return "ok"


CONTROLLED_HARNESS_SCENARIOS: tuple[dict[str, Any], ...] = (
    {"id": "chat_guidance", "task_type": "conversation", "expected_profile": "chat", "visible_tools": ("read_file", "edit_file", "verify")},
    {"id": "research_sources", "task_type": "web_research", "tool_groups": ("web_research",), "expected_profile": "research", "visible_tools": ("web_search", "web_fetch", "edit_file")},
    {"id": "coding_analysis", "task_type": "workspace_read", "tool_groups": ("workspace_read",), "expected_profile": "coding", "visible_tools": ("read_file", "edit_file", "verify")},
    {"id": "coding_change", "task_type": "code_change", "tool_groups": ("workspace_read", "workspace_write"), "require_file_change": True, "expected_profile": "coding", "visible_tools": ("edit_file", "verify", "mcp_config")},
    {"id": "ops_approval", "task_type": "operations", "expected_profile": "ops", "visible_tools": ("credential_store", "mcp_example_tool", "browser_click"), "approval_tools": ("mcp_example_tool", "browser_click")},
)


def run_controlled_harness_scenarios(cases: tuple[dict[str, Any], ...] | None = None) -> dict[str, Any]:
    selected = CONTROLLED_HARNESS_SCENARIOS if cases is None else cases
    evaluated = [evaluate_controlled_harness_scenario(case) for case in selected]
    checks = [check for case in evaluated for check in case["checks"]]
    return {
        "ok": all(case["ok"] for case in evaluated),
        "live": False,
        "kind": "controlled_harness_scenarios",
        "cases": evaluated,
        "summary": {
            "passed_cases": sum(1 for case in evaluated if case["ok"]),
            "total_cases": len(evaluated),
            "passed_checks": sum(1 for check in checks if check["ok"]),
            "total_checks": len(checks),
        },
    }


def evaluate_controlled_harness_scenario(case: dict[str, Any]) -> dict[str, Any]:
    contract = _scenario_contract(case)
    profile = HarnessProfileService().from_contract(contract)
    policy = HarnessPolicyService().select(profile)
    registry = _scenario_registry()
    filtered = HarnessPolicyService().build_tool_registry(registry, policy)
    visible = set(filtered.tool_names)
    expected_sensor_ids = expected_sensor_ids_for_task_type(profile.task_type)
    checks = [
        _check("profile", profile.name == case["expected_profile"], f"Selected {profile.name}."),
        _check("contract", bool(contract.to_metadata()), f"Contract task type {contract.task_type}."),
        _check(
            "expected_sensors",
            bool(expected_sensor_ids),
            f"Expected sensors: {', '.join(expected_sensor_ids) or '-'}.",
        ),
    ]
    checks.extend(_check(f"visible:{tool}", tool in visible, f"{tool} visible={tool in visible}.") for tool in case.get("visible_tools", ()))
    checks.extend(_check(f"blocked:{tool}", tool not in visible, f"{tool} visible={tool in visible}.") for tool in case.get("blocked_tools", ()))
    for tool in case.get("approval_tools", ()):
        decision = policy.to_permission_policy().check(tool, {})
        checks.append(_check(f"approval:{tool}", decision.requires_approval, decision.reason or f"{tool} approval={decision.requires_approval}."))
    return {
        "id": case["id"],
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "profile": profile.to_metadata(),
        "policy": policy.to_metadata(),
        "visible_tools": sorted(visible),
        "contract": contract.to_metadata(),
        "expected_sensor_ids": list(expected_sensor_ids),
    }


def _scenario_contract(case: dict[str, Any]) -> TaskContract:
    requirements = [
        EvidenceRequirement(kind="tool_group", tool_group=str(tool_group))
        for tool_group in case.get("tool_groups", ())
    ]
    if case.get("require_file_change"):
        requirements.append(EvidenceRequirement(kind="file_change"))
    return TaskContract(
        objective=str(case.get("prompt") or case["id"]),
        task_type=str(case.get("task_type") or "conversation"),
        requirements=tuple(requirements),
        allow_no_tool_final=not requirements,
        contract_sources=("controlled_eval",),
    )


def _scenario_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_ScenarioTool("read_file", {"read"}))
    registry.register(_ScenarioTool("web_search", {"network"}))
    registry.register(_ScenarioTool("web_fetch", {"network"}))
    registry.register(_ScenarioTool("edit_file", {"write"}))
    registry.register(_ScenarioTool("verify", {"execute"}))
    registry.register(_ScenarioTool("credential_store", {"write", "configuration"}))
    registry.register(_ScenarioTool("mcp_config", {"mcp", "configuration"}))
    registry.register(_ScenarioTool("mcp_example_tool", {"mcp", "external_side_effect"}))
    registry.register(_ScenarioTool("browser_click", {"network", "external_side_effect"}))
    return registry


def _check(id_: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"id": id_, "ok": bool(ok), "detail": detail}
