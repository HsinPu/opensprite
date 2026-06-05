from opensprite.agent.planning_mode import resolve_planning_mode
from opensprite.agent.task_contract import TaskContract
from opensprite.tools import Tool, ToolRegistry


class _NamedTool(Tool):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def _execute(self, **kwargs):
        return self._name


def test_resolve_planning_mode_returns_disabled_state_without_planning_contract():
    state = resolve_planning_mode()

    assert state.enabled is False
    assert state.overlay == ""
    assert state.tool_registry is None


def test_resolve_planning_mode_does_not_use_text_without_contract():
    state = resolve_planning_mode()

    assert state.enabled is False
    assert state.overlay == ""
    assert state.tool_registry is None


def test_resolve_planning_mode_returns_overlay_and_restricted_registry_from_contract():
    registry = ToolRegistry()
    registry.register(_NamedTool("read_file"))
    registry.register(_NamedTool("write_file"))
    registry.register(_NamedTool("exec"))
    registry.register(_NamedTool("web_fetch"))
    registry.register(_NamedTool("batch"))

    state = resolve_planning_mode(
        base_registry=registry,
        task_contract=TaskContract(
            objective="Propose the next step.",
            task_type="planning",
        ),
    )

    assert state.enabled is True
    assert state.overlay.startswith("# Planning Mode")
    assert state.tool_registry is not None
    tool_names = set(state.tool_registry.tool_names)
    assert tool_names == {"read_file", "web_fetch", "batch"}
    assert state.tool_registry.permission_resolution_metadata["kind"] == "planning_mode"
    assert state.tool_registry.permission_resolution_metadata["effective_risks"]["allowed_risk_levels"] == [
        "read",
        "network",
    ]
