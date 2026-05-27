from opensprite.agent.harness_policy import HarnessPolicyService
from opensprite.agent.harness_profile import HarnessProfile
from opensprite.agent.tool_access import ToolAccessResolver
from opensprite.tools.base import Tool
from opensprite.tools.permissions import ToolPermissionPolicy
from opensprite.tools.registry import ToolRegistry


class DummyTool(Tool):
    def __init__(self, name: str, *, risk_levels: frozenset[str] | None = None):
        self._name = name
        self._risk_levels = risk_levels

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Dummy {self._name} tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def risk_levels(self) -> frozenset[str] | None:
        return self._risk_levels

    async def _execute(self, **kwargs) -> str:
        return "ok"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("read_file", "web_search", "apply_patch", "task_update", "batch"):
        registry.register(DummyTool(name))
    return registry


def test_tool_access_resolver_returns_constrained_registry_and_metadata():
    harness_policy = HarnessPolicyService().select(HarnessProfile(name="chat", task_type="question"))

    resolution = ToolAccessResolver().resolve(_registry(), harness_policy)

    assert resolution.registry.tool_names == ["read_file", "batch"]
    assert resolution.registry.permission_policy is resolution.effective_policy
    assert resolution.registry.permission_resolution_metadata == resolution.metadata
    assert resolution.metadata["harness_policy"]["name"] == "chat_read_policy"
    assert resolution.metadata["effective_policy"]["kind"] == "composite"
    assert resolution.metadata["tool_access"]["registered_tool_count"] == 5
    assert resolution.metadata["tool_access"]["exposed_tools"] == ["read_file", "batch"]
    blocked = {item["name"]: item for item in resolution.metadata["tool_access"]["blocked_tools"]}
    assert blocked["web_search"]["reason"] == "risk level(s) denied: network"
    assert blocked["task_update"]["reason"] == "risk level(s) denied: memory, write"


def test_tool_access_resolver_composes_profile_override_with_harness_policy():
    harness_policy = HarnessPolicyService().select(HarnessProfile(name="research", task_type="web_research"))
    profile_override = ToolPermissionPolicy(allowed_risk_levels=["read"])

    resolution = ToolAccessResolver().resolve(_registry(), harness_policy, profile_override)

    assert resolution.registry.tool_names == ["read_file", "batch"]
    assert resolution.metadata["profile_override"]["allowed_risk_levels"] == ["read"]
    assert "profile permission override" in resolution.metadata["constraints_applied"]
    assert resolution.metadata["tool_access"]["blocked_tool_count"] == 3
