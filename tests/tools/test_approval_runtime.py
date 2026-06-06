import asyncio

from opensprite.runs.events import PERMISSION_GRANTED_EVENT, PERMISSION_REQUESTED_EVENT
from opensprite.tools.approval import PermissionRequest, PermissionRequestManager
from opensprite.tools.approval_runtime import AgentPermissionService, PermissionEventRecorder
from opensprite.tools.permissions import PermissionDecision


def test_permission_event_recorder_emits_run_event_payload():
    async def scenario():
        events = []

        async def emit_run_event(session_id, run_id, event_type, payload, *, channel=None, external_chat_id=None):
            events.append((session_id, run_id, event_type, payload, channel, external_chat_id))

        recorder = PermissionEventRecorder(
            emit_run_event=emit_run_event,
            format_log_preview=lambda text, max_chars: text[:max_chars],
        )
        request = PermissionRequest(
            request_id="perm_test",
            tool_name="apply_patch",
            params={"path": "src/app.py", "ok": True},
            reason="needs write approval",
            created_at=1.0,
            expires_at=2.0,
            session_id="session-1",
            run_id="run-1",
            channel="web",
            external_chat_id="chat-1",
            risk_levels=["write"],
            preview="src/app.py",
        )

        await recorder.emit("permission.requested", request)
        return events

    events = asyncio.run(scenario())

    assert len(events) == 1
    session_id, run_id, event_type, payload, channel, external_chat_id = events[0]
    assert session_id == "session-1"
    assert run_id == "run-1"
    assert event_type == "permission.requested"
    assert channel == "web"
    assert external_chat_id == "chat-1"
    assert payload["request_id"] == "perm_test"
    assert payload["tool_name"] == "apply_patch"
    assert payload["risk_levels"] == ["write"]
    assert payload["args_preview"] == '{"ok": true, "path": "src/app.py"}'


def test_agent_permission_service_adds_current_run_context_to_request():
    async def scenario():
        manager_events = []

        async def on_event(event_type, request):
            manager_events.append((event_type, request))

        async def emit_run_event(*args, **kwargs):
            return None

        manager = PermissionRequestManager(timeout_seconds=1, on_event=on_event)
        recorder = PermissionEventRecorder(
            emit_run_event=emit_run_event,
            format_log_preview=lambda text, max_chars: text[:max_chars],
        )
        service = AgentPermissionService(
            requests=manager,
            events=recorder,
            current_session_id=lambda: "session-1",
            current_run_id=lambda: "run-1",
            current_channel=lambda: "web",
            current_external_chat_id=lambda: "chat-1",
        )
        decision = PermissionDecision(
            allowed=True,
            reason="requires approval",
            requires_approval=True,
            risk_levels=("write",),
        )
        task = asyncio.create_task(
            service.handle_tool_permission_request("apply_patch", {"path": "src/app.py"}, decision)
        )
        for _ in range(100):
            pending = service.pending_requests()
            if pending:
                break
            await asyncio.sleep(0.01)
        request = pending[0]

        approved = await service.approve_request(request.request_id)
        result = await task
        return request, approved, result, manager_events

    request, approved, result, manager_events = asyncio.run(scenario())

    assert request.session_id == "session-1"
    assert request.run_id == "run-1"
    assert request.channel == "web"
    assert request.external_chat_id == "chat-1"
    assert request.risk_levels == ["write"]
    assert approved is request
    assert result.approved is True
    assert [event_type for event_type, _ in manager_events] == [
        PERMISSION_REQUESTED_EVENT,
        PERMISSION_GRANTED_EVENT,
    ]
