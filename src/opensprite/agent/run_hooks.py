"""Execution hook factories for run telemetry and interim outbound messages."""

from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable

from ..tools.verify import classify_verification_result
from ..bus.events import OutboundMessage
from ..utils import json_safe_payload


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
        if tool_name in {"read_skill", "delegate"}:
            return True
        return tool_name.startswith("mcp_")

    @staticmethod
    def format_tool_progress_message(tool_name: str, tool_args: dict[str, Any]) -> str:
        """User-facing one-line status for skill, subagent, and MCP tool execution."""
        args = tool_args or {}
        if tool_name == "read_skill":
            name = args.get("skill_name") or "?"
            return f"正在讀取技能〈{name}〉…"
        if tool_name == "delegate":
            task_id = args.get("task_id")
            ptype = args.get("prompt_type") or "writer"
            if task_id:
                return f"正在續跑子代理任務（{task_id}）…"
            return f"正在委派子代理（{ptype}）…"
        if tool_name.startswith("mcp_"):
            tail = tool_name[4:] if tool_name.startswith("mcp_") else tool_name
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
            if tool_name == "verify":
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
            ok = not result_text.lstrip().startswith("Error:")
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
            if tool_name == "verify":
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
    ) -> Callable[[str], Awaitable[None]] | None:
        """Publish run telemetry and interim outbound status during long LLM waits."""
        if not enabled or run_id is None:
            return None
        bus = self._message_bus_getter()
        ch = channel
        tid = str(external_chat_id) if external_chat_id is not None else None
        sid = session_id
        rid = run_id

        async def _hook(text: str) -> None:
            payload = {"message": text}
            if "retry" in str(text or "").lower() or "重試" in str(text or ""):
                payload["status"] = "retry"
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
