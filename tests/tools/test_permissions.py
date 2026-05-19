import asyncio

from opensprite.tools.approval import PermissionRequestManager, classify_permission_request
from opensprite.tools.base import Tool
from opensprite.tools.permissions import ToolPermissionPolicy
from opensprite.tools.registry import ToolRegistry


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

    assert result == "Error: Tool 'exec' blocked by permission policy: tool 'exec' is listed in denied_tools."


def test_registry_blocks_denied_risk_levels():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(denied_risk_levels=["network"])
    )
    registry.register(EchoTool("web_fetch"))

    assert registry.tool_names == []
    result = asyncio.run(registry.execute("web_fetch", {}))

    assert result == "Error: Tool 'web_fetch' blocked by permission policy: risk level(s) denied: network."


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
        "Error: Tool 'custom_unknown' blocked by permission policy: "
        "risk level(s) not allowed: external_side_effect."
    )


def test_registry_restricts_allowed_tools_by_glob():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(allowed_tools=["read_*", "grep_files"])
    )
    registry.register(EchoTool("read_file"))
    registry.register(EchoTool("grep_files"))
    registry.register(EchoTool("write_file"))

    assert registry.tool_names == ["read_file", "grep_files"]
    assert asyncio.run(registry.execute("write_file", {})).startswith(
        "Error: Tool 'write_file' blocked by permission policy: tool 'write_file' is not in allowed_tools."
    )


def test_approval_required_policy_blocks_when_mode_is_unset():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(approval_required_tools=["apply_patch"])
    )
    registry.register(EchoTool("apply_patch"))

    result = asyncio.run(registry.execute("apply_patch", {}))

    assert registry.tool_names == []
    assert result == "Error: Tool 'apply_patch' blocked by permission policy: tool 'apply_patch' requires user approval."


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

    assert result == "Error: Tool 'apply_patch' blocked by permission policy: tool 'apply_patch' requires user approval."


def test_approval_required_policy_exposes_but_blocks_execution_in_ask_mode():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(approval_mode="ask", approval_required_tools=["apply_patch"])
    )
    registry.register(EchoTool("apply_patch"))

    result = asyncio.run(registry.execute("apply_patch", {}))

    assert registry.tool_names == ["apply_patch"]
    assert result == "Error: Tool 'apply_patch' blocked by permission policy: tool 'apply_patch' requires user approval."


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

    assert result == "Error: Tool 'apply_patch' blocked by permission policy: user denied approval."
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

    assert result == "Error: Tool 'apply_patch' blocked by permission policy: permission request timed out."
    assert events == [
        ("permission_requested", "pending", False),
        ("permission_denied", "denied", True),
    ]
    assert pending == []
