"""Controlled harness scenario evaluations.

These cases exercise the profile -> policy -> tool exposure path without calling
an external LLM or live web service.
"""

from __future__ import annotations

from typing import Any

from ..agent.harness_policy import HarnessPolicyService
from ..agent.harness_profile import HarnessProfileService
from ..agent.task_contract import TaskContractService
from ..agent.task_intent import TaskIntentService
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
    {"id": "chat_read_only", "prompt": "Explain what an agent harness does.", "expected_profile": "chat", "blocked_tools": ("edit_file", "exec"), "visible_tools": ("read_file",)},
    {"id": "research_sources", "prompt": "Search the web and cite sources for current OpenSprite news.", "expected_profile": "research", "visible_tools": ("web_search", "web_fetch"), "blocked_tools": ("edit_file",)},
    {"id": "coding_analysis", "prompt": "Review src/opensprite/agent/harness_profile.py and explain the logic.", "expected_profile": "coding", "visible_tools": ("read_file",), "blocked_tools": ("edit_file", "exec")},
    {"id": "coding_change", "prompt": "Fix tests in src/opensprite/agent/harness_profile.py and run pytest.", "expected_profile": "coding", "visible_tools": ("edit_file", "verify"), "blocked_tools": ("mcp_config",)},
    {"id": "ops_approval", "prompt": "Update the MCP server configuration and restart the service after approval.", "expected_profile": "ops", "visible_tools": ("credential_store",), "approval_tools": ("mcp_example_tool", "browser_click")},
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
    intent = TaskIntentService().classify(str(case["prompt"]))
    profile = HarnessProfileService().select(intent)
    policy = HarnessPolicyService().select(profile)
    registry = _scenario_registry()
    filtered = HarnessPolicyService().build_tool_registry(registry, policy)
    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=str(case["prompt"]),
        harness_profile=profile,
    )
    visible = set(filtered.tool_names)
    checks = [
        _check("profile", profile.name == case["expected_profile"], f"Selected {profile.name}."),
        _check("contract", bool(contract.to_metadata()), f"Contract task type {contract.task_type}."),
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
    }


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
