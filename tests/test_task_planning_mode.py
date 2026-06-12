from opensprite.agent.task.contract import TaskContract
from opensprite.agent.task.planning_mode import resolve_planning_mode
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
    metadata = state.tool_registry.tool_selection_metadata
    assert metadata["kind"] == "planning_mode"
    assert set(metadata["tool_selection"]["selected_tools"]) == {"read_file", "web_fetch", "batch"}
    missing_names = {item["name"] for item in metadata["tool_selection"]["missing_required_tools"]}
    assert "analyze_image" in missing_names
    assert "write_file" not in missing_names
    assert "exec" not in missing_names
