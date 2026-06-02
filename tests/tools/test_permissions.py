import asyncio

from opensprite.tools.approval import PermissionRequestManager, classify_permission_request
from opensprite.tools.base import Tool
from opensprite.tools.permissions import ToolPermissionPolicy
from opensprite.tools.registry import ToolRegistry
from opensprite.tools.result_status import classify_tool_result_status


class EchoTool(Tool):
    def __init__(self, name: str, *, risk_levels: frozenset[str] | None = None):
        self._name = name
        self._risk_levels = risk_levels

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def risk_levels(self) -> frozenset[str] | None:
        return self._risk_levels

    async def _execute(self, **kwargs):
        return f"ran:{self.name}"


async def _wait_for_pending(manager: PermissionRequestManager):
    for _ in range(100):
        pending = manager.pending_requests()
        if pending:
            return pending[0]
        await asyncio.sleep(0.001)
    raise AssertionError("permission request was not created")


def _assert_permission_block(result: str, reason: str) -> None:
    status = classify_tool_result_status(result)
    assert status.ok is False
    assert status.error_type == "ToolPermissionError"
    assert status.category == "permission_block"
    assert reason in status.error


def test_registry_hides_and_blocks_denied_tools():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(denied_tools=["exec"])
    )
    registry.register(EchoTool("read_file"))
    registry.register(EchoTool("exec"))

    assert registry.tool_names == ["read_file"]
    definitions = registry.get_definitions()
    assert [item["function"]["name"] for item in definitions] == ["read_file"]

    result = asyncio.run(registry.execute("exec", {}))

    assert result == (
        "Error: Tool 'exec' is not available in this turn. Available tools: read_file. "
        "Do not call unavailable tools again; answer directly or use an available tool."
    )


def test_registry_blocks_denied_risk_levels():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(denied_risk_levels=["network"])
    )
    registry.register(EchoTool("web_fetch"))

    assert registry.tool_names == []
    result = asyncio.run(registry.execute("web_fetch", {}))

    assert result == (
        "Error: Tool 'web_fetch' is not available in this turn. Available tools: none. "
        "Do not call unavailable tools again; answer directly or use an available tool."
    )


def test_registry_emits_permission_decision_trace_events():
    async def scenario():
        events = []
        registry = ToolRegistry(
            permission_policy=ToolPermissionPolicy(allowed_risk_levels=["read"], denied_tools=["exec"])
        )
        registry.register(EchoTool("read_file"))
        registry.register(EchoTool("exec"))

        async def on_decision(event_type, tool_name, payload):
            events.append((event_type, tool_name, payload))

        registry.set_permission_decision_hook(on_decision)
        allowed_result = await registry.execute("read_file", {})
        denied_result = await registry.execute("exec", {})
        return allowed_result, denied_result, events

    allowed_result, denied_result, events = asyncio.run(scenario())

    assert allowed_result == "ran:read_file"
    assert denied_result == (
        "Error: Tool 'exec' is not available in this turn. Available tools: read_file. "
        "Do not call unavailable tools again; answer directly or use an available tool."
    )
    assert [event[0] for event in events] == [
        "tool_permission.checked",
        "tool_permission.allowed",
        "tool_permission.not_exposed",
    ]
    assert events[0][2]["risk_levels"] == ["read"]
    assert events[0][2]["exposed"] is True
    assert events[0][2]["exposure"] == "exposed"
    assert events[1][2]["decision"] == "allowed"
    assert events[2][2]["decision"] == "denied"
    assert events[2][2]["exposed"] is False
    assert events[2][2]["exposure"] == "not_exposed"
    assert events[2][2]["matched_denied_tools"] == ["exec"]


def test_browser_actions_are_network_side_effect_tools():
    assert ToolPermissionPolicy.risk_levels_for_tool("browser_navigate") == frozenset(
        {"network", "external_side_effect"}
    )
    assert ToolPermissionPolicy.risk_levels_for_tool("browser_snapshot") == frozenset({"network"})


def test_registry_uses_declared_tool_risk_levels():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(allowed_risk_levels=["read"])
    )
    registry.register(EchoTool("custom_read", risk_levels=frozenset({"read"})))
    registry.register(EchoTool("custom_unknown"))

    assert registry.tool_names == ["custom_read"]
    assert asyncio.run(registry.execute("custom_read", {})) == "ran:custom_read"
    assert asyncio.run(registry.execute("custom_unknown", {})) == (
        "Error: Tool 'custom_unknown' is not available in this turn. Available tools: custom_read. "
        "Do not call unavailable tools again; answer directly or use an available tool."
    )


def test_registry_evidence_includes_permission_block_metadata():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(allowed_risk_levels=["read"])
    )
    registry.register(EchoTool("custom_write", risk_levels=frozenset({"write"})))

    result = asyncio.run(registry.execute("custom_write", {}))
    evidence = registry.build_evidence("custom_write", {}, result, ok=False)

    assert evidence.ok is False
    assert evidence.metadata["permission"]["blocked"] is True
    assert evidence.metadata["permission"]["exposure"] == "not_exposed"
    assert evidence.metadata["permission"]["reason"] == "risk level(s) not allowed: write"
    assert evidence.metadata["permission"]["risk_levels"] == ["write"]


def test_registry_restricts_allowed_tools_by_glob():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(allowed_tools=["read_*", "grep_files"])
    )
    registry.register(EchoTool("read_file"))
    registry.register(EchoTool("grep_files"))
    registry.register(EchoTool("write_file"))

    assert registry.tool_names == ["read_file", "grep_files"]
    assert asyncio.run(registry.execute("write_file", {})).startswith(
        "Error: Tool 'write_file' is not available in this turn. Available tools: read_file, grep_files."
    )


def test_approval_required_policy_blocks_when_mode_is_unset():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(approval_required_tools=["apply_patch"])
    )
    registry.register(EchoTool("apply_patch"))

    result = asyncio.run(registry.execute("apply_patch", {}))

    assert registry.tool_names == []
    assert result == (
        "Error: Tool 'apply_patch' is not available in this turn. Available tools: none. "
        "Do not call unavailable tools again; answer directly or use an available tool."
    )


def test_approval_required_policy_allows_in_auto_mode():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(approval_mode="auto", approval_required_tools=["apply_patch"])
    )
    registry.register(EchoTool("apply_patch"))

    result = asyncio.run(registry.execute("apply_patch", {}))

    assert registry.tool_names == ["apply_patch"]
    assert result == "ran:apply_patch"


def test_approval_required_policy_blocks_in_block_mode():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(approval_mode="block", approval_required_tools=["apply_patch"])
    )
    registry.register(EchoTool("apply_patch"))

    result = asyncio.run(registry.execute("apply_patch", {}))

    assert result == (
        "Error: Tool 'apply_patch' is not available in this turn. Available tools: none. "
        "Do not call unavailable tools again; answer directly or use an available tool."
    )


def test_approval_required_policy_exposes_but_blocks_execution_in_ask_mode():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(approval_mode="ask", approval_required_tools=["apply_patch"])
    )
    registry.register(EchoTool("apply_patch"))

    result = asyncio.run(registry.execute("apply_patch", {}))

    assert registry.tool_names == ["apply_patch"]
    _assert_permission_block(result, "tool 'apply_patch' requires user approval")


def test_approval_required_policy_waits_for_approval_in_ask_mode():
    async def scenario():
        events = []

        async def on_event(event_type, request):
            events.append((event_type, request.request_id, request.status))

        manager = PermissionRequestManager(timeout_seconds=1, on_event=on_event)
        registry = ToolRegistry(
            permission_policy=ToolPermissionPolicy(approval_mode="ask", approval_required_tools=["apply_patch"])
        )
        registry.register(EchoTool("apply_patch"))
        registry.set_permission_request_handler(
            lambda name, params, decision: manager.request(
                tool_name=name,
                params=params,
                reason=decision.reason,
            )
        )

        task = asyncio.create_task(registry.execute("apply_patch", {}))
        request = await _wait_for_pending(manager)
        assert not task.done()

        await manager.approve_once(request.request_id)
        result = await task
        return result, events, manager.pending_requests()

    result, events, pending = asyncio.run(scenario())

    assert result == "ran:apply_patch"
    assert [event[0] for event in events] == ["permission_requested", "permission_granted"]
    assert events[0][2] == "pending"
    assert events[1][2] == "approved"
    assert pending == []


def test_permission_request_classification_fields():
    edit = classify_permission_request("apply_patch", {"path": "src/app.py"})
    assert edit == {
        "action_type": "edit",
        "risk_level": "medium",
        "risk_levels": ["write"],
        "resource": "src/app.py",
        "preview": "src/app.py",
        "recommended_decision": "approve",
    }

    push = classify_permission_request("exec", {"command": "git push"})
    assert push["action_type"] == "push"
    assert push["risk_level"] == "high"
    assert push["recommended_decision"] == "approve"

    destructive = classify_permission_request("exec", {"command": "git reset --hard HEAD"})
    assert destructive["action_type"] == "destructive"
    assert destructive["risk_level"] == "high"
    assert destructive["recommended_decision"] == "deny"
    assert destructive["destructive_reason"] == "git reset --hard"

    wrapped_destructive = classify_permission_request(
        "exec",
        {"command": 'powershell -Command "Remove-Item -Recurse ."'},
    )
    assert wrapped_destructive["action_type"] == "destructive"
    assert wrapped_destructive["recommended_decision"] == "deny"
    assert wrapped_destructive["destructive_reason"] == "powershell -Command -> remove-item recursive/forced delete"

    inline_wrapper_destructive = classify_permission_request(
        "exec",
        {"command": 'bash -c "git reset --hard HEAD"'},
    )
    assert inline_wrapper_destructive["action_type"] == "destructive"
    assert inline_wrapper_destructive["recommended_decision"] == "deny"
    assert inline_wrapper_destructive["destructive_reason"] == "bash -c -> git reset --hard"


def test_permission_request_classification_uses_decision_risk_levels():
    classification = classify_permission_request(
        "custom_tool",
        {"query": "status"},
        risk_levels=["read"],
    )

    assert classification["action_type"] == "read"
    assert classification["risk_level"] == "low"
    assert classification["risk_levels"] == ["read"]


def test_permission_request_manager_uses_decision_risk_levels():
    async def scenario():
        manager = PermissionRequestManager(timeout_seconds=1)
        task = asyncio.create_task(
            manager.request(
                tool_name="custom_tool",
                params={"query": "status"},
                reason="approval required",
                risk_levels=["read"],
            )
        )
        request = await _wait_for_pending(manager)

        await manager.approve_once(request.request_id)
        result = await task
        return result, request

    result, request = asyncio.run(scenario())

    assert result.approved is True
    assert request.action_type == "read"
    assert request.risk_level == "low"
    assert request.risk_levels == ["read"]


def test_approval_required_policy_denies_pending_request_in_ask_mode():
    async def scenario():
        manager = PermissionRequestManager(timeout_seconds=1)
        registry = ToolRegistry(
            permission_policy=ToolPermissionPolicy(approval_mode="ask", approval_required_tools=["apply_patch"])
        )
        registry.register(EchoTool("apply_patch"))
        registry.set_permission_request_handler(
            lambda name, params, decision: manager.request(
                tool_name=name,
                params=params,
                reason=decision.reason,
            )
        )

        task = asyncio.create_task(registry.execute("apply_patch", {}))
        request = await _wait_for_pending(manager)

        await manager.deny(request.request_id)
        result = await task
        return result, manager.pending_requests()

    result, pending = asyncio.run(scenario())

    _assert_permission_block(result, "user denied approval")
    assert pending == []


def test_approval_required_policy_times_out_pending_request_in_ask_mode():
    async def scenario():
        events = []

        async def on_event(event_type, request):
            events.append((event_type, request.status, request.timed_out))

        manager = PermissionRequestManager(timeout_seconds=0.01, on_event=on_event)
        registry = ToolRegistry(
            permission_policy=ToolPermissionPolicy(approval_mode="ask", approval_required_tools=["apply_patch"])
        )
        registry.register(EchoTool("apply_patch"))
        registry.set_permission_request_handler(
            lambda name, params, decision: manager.request(
                tool_name=name,
                params=params,
                reason=decision.reason,
            )
        )

        result = await registry.execute("apply_patch", {})
        return result, events, manager.pending_requests()

    result, events, pending = asyncio.run(scenario())

    _assert_permission_block(result, "permission request timed out")
    assert events == [
        ("permission_requested", "pending", False),
        ("permission_denied", "denied", True),
    ]
    assert pending == []
