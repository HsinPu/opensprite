"""Execution hook factories for run telemetry and interim outbound messages."""

from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable

from ..tool_names import (
    DELEGATE_MANY_TOOL_NAME,
    DELEGATE_TOOL_NAME,
    READ_SKILL_TOOL_NAME,
    RUN_WORKFLOW_TOOL_NAME,
)
from ..tools.verify import classify_verification_result
from ..bus.events import OutboundMessage
from ..utils import json_safe_payload
from ..tools.result_status import classify_tool_result_status
from .mcp_tool_policy import (
    is_mcp_tool_name,
    mcp_tool_display_name,
    tool_warrants_progress_notice as policy_tool_warrants_progress_notice,
)
from .verification_policy import is_verification_tool_name


_TRACE_TEXT_FIELDS = {
    "type",
    "query",
    "url",
    "final_url",
    "provider",
    "backend",
    "search_provider",
    "search_backend",
    "configured_provider",
    "extractor",
    "error",
}

_TRACE_COUNT_FIELDS = {
    "source_count",
    "fetched_count",
    "search_result_count",
    "returned_items",
}


def _trace_text(value: Any, *, max_chars: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) > max_chars:
        return f"{text[: max_chars - 3]}..."
    return text


def _trace_count(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(str(value or "").lstrip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _trace_attempt_payload(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    attempts = payload.get(key)
    if not isinstance(attempts, list):
        return None
    candidates = [attempt for attempt in attempts if isinstance(attempt, dict)]
    if not candidates:
        return None
    for attempt in candidates:
        if attempt.get("ok") is True:
            return attempt
    return candidates[0]


def _tool_result_trace_metadata(result_text: str) -> dict[str, Any]:
    """Extract compact traceable fields from structured tool results."""
    payload = _json_object(result_text)
    if payload is None:
        return {}

    metadata: dict[str, Any] = {}
    for field in _TRACE_TEXT_FIELDS:
        value = _trace_text(payload.get(field))
        if value:
            metadata[field] = value
    for field in _TRACE_COUNT_FIELDS:
        count = _trace_count(payload.get(field))
        if count is not None:
            metadata[field] = count

    items = payload.get("items")
    if isinstance(items, list):
        metadata.setdefault("returned_items", len(items))

    sources = payload.get("sources")
    if isinstance(sources, list):
        metadata.setdefault("source_count", len(sources))
        for source in sources:
            if not isinstance(source, dict):
                continue
            provider = _trace_text(source.get("search_provider") or source.get("provider"))
            backend = _trace_text(source.get("search_backend") or source.get("backend"))
            if provider:
                metadata.setdefault("search_provider", provider)
                metadata.setdefault("provider", provider)
            if backend:
                metadata.setdefault("search_backend", backend)
                metadata.setdefault("backend", backend)
            if provider or backend:
                break

    for attempt_key in ("search_attempts", "query_attempts"):
        attempt = _trace_attempt_payload(payload, attempt_key)
        if not attempt:
            continue
        provider = _trace_text(attempt.get("provider") or attempt.get("configured_provider"))
        backend = _trace_text(attempt.get("backend"))
        if provider:
            metadata.setdefault("provider", provider)
        if backend:
            metadata.setdefault("backend", backend)

    if metadata.get("provider") and not metadata.get("search_provider"):
        metadata["search_provider"] = metadata["provider"]
    if metadata.get("backend") and not metadata.get("search_backend"):
        metadata["search_backend"] = metadata["backend"]
    return metadata


def _tool_error_trace_metadata(result_text: str) -> dict[str, Any]:
    """Extract structured error fields from failed plain-text tool results."""
    return classify_tool_result_status(result_text).error_metadata()


class RunHookService:
    """Builds callbacks passed into the LLM/tool execution engine."""

    def __init__(
        self,
        *,
        message_bus_getter: Callable[[], Any],
        add_run_part: Callable[..., Awaitable[None]],
        emit_run_event: Callable[..., Awaitable[None]],
        format_log_preview: Callable[..., str],
    ):
        self._message_bus_getter = message_bus_getter
        self._add_run_part = add_run_part
        self._emit_run_event = emit_run_event
        self._format_log_preview = format_log_preview
        self._tool_started_at: dict[tuple[str, str, str], float] = {}

    @staticmethod
    def _tool_lifecycle_key(
        session_id: str,
        run_id: str,
        tool_call_id: str | None,
        tool_name: str,
        iteration: int | None,
    ) -> tuple[str, str, str]:
        identifier = tool_call_id or f"{tool_name}:{iteration or 0}"
        return (session_id, run_id, identifier)

    @staticmethod
    def tool_warrants_progress_notice(tool_name: str) -> bool:
        """Whether to send a short interim message before this tool runs."""
        return policy_tool_warrants_progress_notice(tool_name)

    @staticmethod
    def format_tool_progress_message(tool_name: str, tool_args: dict[str, Any]) -> str:
        """User-facing one-line status for skill, subagent, and MCP tool execution."""
        args = tool_args or {}
        if tool_name == READ_SKILL_TOOL_NAME:
            name = args.get("skill_name") or "?"
            return f"正在讀取技能〈{name}〉…"
        if tool_name == DELEGATE_TOOL_NAME:
            task_id = args.get("task_id")
            ptype = args.get("prompt_type") or "writer"
            if task_id:
                return f"正在續跑子代理任務（{task_id}）…"
            return f"正在委派子代理（{ptype}）…"
        if tool_name == DELEGATE_MANY_TOOL_NAME:
            tasks = args.get("tasks") if isinstance(args.get("tasks"), list) else []
            return f"正在平行委派 {max(1, len(tasks))} 個子代理任務…"
        if tool_name == RUN_WORKFLOW_TOOL_NAME:
            workflow = args.get("workflow") or args.get("workflow_id") or "workflow"
            start_step = args.get("start_step") or args.get("startStep")
            if start_step:
                return f"正在續跑固定工作流（{workflow}:{start_step}）…"
            return f"正在執行固定工作流（{workflow}）…"
        if is_mcp_tool_name(tool_name):
            tail = mcp_tool_display_name(tool_name)
            return f"正在呼叫 MCP：{tail}…"
        return "處理中…"

    def make_tool_progress_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, dict[str, Any]], Awaitable[None]] | None:
        """Publish run telemetry and a brief outbound status before selected tools run."""
        if not enabled or run_id is None:
            return None
        bus = self._message_bus_getter()
        ch = channel
        tid = str(external_chat_id) if external_chat_id is not None else None
        sid = session_id
        rid = run_id

        async def _hook(tool_name: str, tool_args: dict[str, Any], tool_call_id: str | None = None, iteration: int | None = None) -> None:
            safe_args = json_safe_payload(tool_args or {})
            args_preview = self._format_log_preview(json.dumps(safe_args, ensure_ascii=False), max_chars=240)
            started_at = time.time()
            self._tool_started_at[self._tool_lifecycle_key(sid, rid, tool_call_id, tool_name, iteration)] = started_at
            metadata = {
                "args": safe_args,
                "args_preview": args_preview,
                "state": "running",
                "started_at": started_at,
            }
            if tool_call_id:
                metadata["tool_call_id"] = tool_call_id
            if iteration is not None:
                metadata["iteration"] = int(iteration)
            await self._add_run_part(
                sid,
                rid,
                "tool_call",
                content=json.dumps(safe_args, ensure_ascii=False, sort_keys=True),
                tool_name=tool_name,
                metadata=metadata,
            )
            await self._emit_run_event(
                sid,
                rid,
                "tool_started",
                {
                    "tool_name": tool_name,
                    "args_preview": args_preview,
                    "tool_call_id": tool_call_id,
                    "iteration": iteration,
                    "state": "running",
                    "started_at": started_at,
                },
                channel=ch,
                external_chat_id=tid,
            )
            if is_verification_tool_name(tool_name):
                await self._emit_run_event(
                    sid,
                    rid,
                    "verification_started",
                    {
                        "action": (tool_args or {}).get("action", "auto"),
                        "path": (tool_args or {}).get("path", "."),
                    },
                    channel=ch,
                    external_chat_id=tid,
                )
            if bus is None or not ch or tid is None or not self.tool_warrants_progress_notice(tool_name):
                return
            text = self.format_tool_progress_message(tool_name, tool_args)
            await bus.publish_outbound(
                OutboundMessage(
                    channel=ch,
                    external_chat_id=tid,
                    session_id=sid,
                    content=text,
                    metadata={"interim": True, "kind": "tool_progress", "tool_name": tool_name},
                )
            )

        return _hook

    def make_tool_result_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, dict[str, Any], str], Awaitable[None]] | None:
        """Publish structured run telemetry after a tool finishes."""
        if not enabled or run_id is None:
            return None
        tid = str(external_chat_id) if external_chat_id is not None else None
        rid = run_id

        async def _hook(
            tool_name: str,
            tool_args: dict[str, Any],
            result: str,
            tool_call_id: str | None = None,
            iteration: int | None = None,
            delegate_task_id: str | None = None,
            delegate_prompt_type: str | None = None,
            state: str | None = None,
            interrupted: bool = False,
        ) -> None:
            safe_args = json_safe_payload(tool_args or {})
            result_text = str(result or "")
            result_preview = self._format_log_preview(result_text, max_chars=240)
            state_text = str(state or "").strip().lower()
            result_status = classify_tool_result_status(result_text, state=state_text)
            ok = result_status.ok
            finished_at = time.time()
            started_at = self._tool_started_at.pop(
                self._tool_lifecycle_key(session_id, rid, tool_call_id, tool_name, iteration),
                None,
            )
            duration_ms = int(max(0.0, finished_at - started_at) * 1000) if started_at is not None else None
            metadata = {
                "args": safe_args,
                "ok": ok,
                "result_len": len(result_text),
                "result_preview": result_preview,
                "state": state or ("completed" if ok else "error"),
                "finished_at": finished_at,
            }
            trace_metadata = _tool_result_trace_metadata(result_text)
            metadata.update(trace_metadata)
            if not ok:
                metadata.update(result_status.error_metadata())
            if started_at is not None:
                metadata["started_at"] = started_at
                metadata["duration_ms"] = duration_ms
            if interrupted:
                metadata["interrupted"] = True
            if tool_call_id:
                metadata["tool_call_id"] = tool_call_id
            if iteration is not None:
                metadata["iteration"] = int(iteration)
            if delegate_task_id:
                metadata["delegate_task_id"] = delegate_task_id
            if delegate_prompt_type:
                metadata["delegate_prompt_type"] = delegate_prompt_type
            await self._add_run_part(
                session_id,
                rid,
                "tool_result",
                content=result_text,
                tool_name=tool_name,
                metadata=metadata,
            )
            await self._emit_run_event(
                session_id,
                rid,
                "tool_result",
                {
                    "tool_name": tool_name,
                    "ok": ok,
                    "result_len": len(result_text),
                    "result_preview": result_preview,
                    **trace_metadata,
                    **({} if ok else _tool_error_trace_metadata(result_text)),
                    "tool_call_id": tool_call_id,
                    "iteration": iteration,
                    "delegate_task_id": delegate_task_id,
                    "delegate_prompt_type": delegate_prompt_type,
                    "state": metadata["state"],
                    "interrupted": interrupted,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "duration_ms": duration_ms,
                },
                channel=channel,
                external_chat_id=tid,
            )
            if is_verification_tool_name(tool_name):
                verification = classify_verification_result(result_text)
                await self._emit_run_event(
                    session_id,
                    rid,
                    "verification_result",
                    {
                        "action": (tool_args or {}).get("action", "auto"),
                        "path": (tool_args or {}).get("path", "."),
                        "ok": ok,
                        "verification_status": verification["status"],
                        "verification_name": verification["name"],
                        "result_preview": result_preview,
                    },
                    channel=channel,
                    external_chat_id=tid,
                )

        return _hook

    def make_llm_status_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[Any], Awaitable[None]] | None:
        """Publish run telemetry and interim outbound status during long LLM waits."""
        if not enabled or run_id is None:
            return None
        bus = self._message_bus_getter()
        ch = channel
        tid = str(external_chat_id) if external_chat_id is not None else None
        sid = session_id
        rid = run_id

        async def _hook(update: Any) -> None:
            if isinstance(update, dict):
                text = str(update.get("message") or "").strip()
                payload = {
                    key: value
                    for key, value in update.items()
                    if key != "message" and value not in (None, "")
                }
                payload["message"] = text
            else:
                text = str(update or "")
                payload = {"message": text}
            await self._emit_run_event(
                sid,
                rid,
                "llm_status",
                payload,
                channel=ch,
                external_chat_id=tid,
            )
            if bus is None or not ch or tid is None:
                return
            await bus.publish_outbound(
                OutboundMessage(
                    channel=ch,
                    external_chat_id=tid,
                    session_id=sid,
                    content=text,
                    metadata={"interim": True, "kind": "llm_wait"},
                )
            )

        return _hook

    def make_llm_delta_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, str, str, int], Awaitable[None]] | None:
        """Publish visible assistant response chunks into the run event stream."""
        if not enabled or run_id is None:
            return None
        ch = channel
        tid = str(external_chat_id) if external_chat_id is not None else None
        sid = session_id
        rid = run_id

        async def _hook(part_id: str, delta: str, state: str = "running", sequence: int = 0) -> None:
            text = str(delta or "")
            normalized_state = str(state or "running")
            if not text and normalized_state == "running":
                return
            await self._emit_run_event(
                sid,
                rid,
                "run_part_delta",
                {
                    "part_id": part_id,
                    "part_type": "assistant_message",
                    "content_delta": text,
                    "state": normalized_state,
                    "sequence": int(sequence),
                },
                channel=ch,
                external_chat_id=tid,
            )

        return _hook

    def make_tool_input_delta_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, str, str, int], Awaitable[None]] | None:
        """Publish streamed tool-call argument chunks into the run event stream."""
        if not enabled or run_id is None:
            return None
        ch = channel
        tid = str(external_chat_id) if external_chat_id is not None else None
        sid = session_id
        rid = run_id

        async def _hook(tool_call_id: str, tool_name: str, delta: str, sequence: int = 0) -> None:
            text = str(delta or "")
            if not text:
                return
            await self._emit_run_event(
                sid,
                rid,
                "tool_input_delta",
                {
                    "tool_call_id": str(tool_call_id or ""),
                    "tool_name": str(tool_name or ""),
                    "input_delta": text,
                    "sequence": int(sequence),
                },
                channel=ch,
                external_chat_id=tid,
            )

        return _hook

    def make_reasoning_delta_hook(
        self,
        *,
        channel: str | None,
        external_chat_id: str | None,
        session_id: str,
        run_id: str | None,
        enabled: bool,
    ) -> Callable[[str, int], Awaitable[None]] | None:
        """Publish provider reasoning chunks into inspector-only run events."""
        if not enabled or run_id is None:
            return None
        ch = channel
        tid = str(external_chat_id) if external_chat_id is not None else None
        sid = session_id
        rid = run_id

        async def _hook(delta: str, sequence: int = 0) -> None:
            text = str(delta or "")
            if not text:
                return
            await self._emit_run_event(
                sid,
                rid,
                "reasoning_delta",
                {
                    "content_delta": text,
                    "sequence": int(sequence),
                    "inspector_only": True,
                },
                channel=ch,
                external_chat_id=tid,
            )

        return _hook
