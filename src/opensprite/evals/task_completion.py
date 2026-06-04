"""Deterministic task-completion evaluation helpers."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from ..bus.message import UserMessage
from ..runs.lifecycle import RUN_CANCELLED_EVENT, RUN_FAILED_EVENT, RUN_FINISHED_EVENT
from ..storage.base import StoredEvalRun


_RESPONSE_PREVIEW_CHARS = 240


TASK_COMPLETION_SMOKE_CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "web_smoke_question",
        "label": "Web smoke check answer",
        "prompt": "OpenSprite 的 web smoke check 怎麼跑？",
        "expected_completion_status": "complete",
        "must_include": ("apps/web", "npm run test:smoke", "smoke check"),
        "must_not_include": ("Playwright", "Vitest"),
        "require_no_tool_error": True,
        "sample_result": {
            "response_text": "在 apps/web 執行 npm run test:smoke；這是 smoke check，不是完整前端測試。",
            "completion_status": "complete",
            "had_tool_error": False,
        },
    },
    {
        "id": "task_completion_question",
        "label": "Task completion criteria answer",
        "prompt": "如何小規模測試 LLM 是否完成使用者任務？",
        "expected_completion_status": "complete",
        "must_include": ("completion_gate", "final response", "must_include"),
        "must_not_include": ("只要有回答就一定正確",),
        "require_no_tool_error": True,
        "sample_result": {
            "response_text": (
                "小規模可以用固定 case：送 prompt 後讀 final response 和 completion_gate，"
                "再用 must_include / must_not_include 與 tool error 檢查打 pass/fail。"
            ),
            "completion_status": "complete",
            "had_tool_error": False,
        },
    },
)

TASK_COMPLETION_LIVE_CASES: tuple[dict[str, Any], ...] = (
    {
        "id": "literal_instruction",
        "label": "Literal instruction answer",
        "prompt": "請只回覆這三個英文詞，且不要加入其他文字：alpha beta gamma",
        "expected_completion_status": "complete",
        "exact_response": "alpha beta gamma",
        "must_include": ("alpha", "beta", "gamma"),
        "must_not_include": ("抱歉", "sorry", "無法"),
        "require_no_tool_error": True,
        "require_run_trace": True,
        "max_response_chars": 120,
    },
    {
        "id": "multi_step_completion",
        "label": "Multi-step completion answer",
        "prompt": (
            "請嚴格輸出三行，不要標題、不要 Markdown 粗體、不要額外說明：\n"
            "1. 問題：用一句話說明這是格式遵循測試\n"
            "2. 可能原因：列出兩個原因，並用「；」分隔\n"
            "3. 結論：已完成三步驟回答\n\n"
            "第三行必須完全以「3. 結論：已完成三步驟回答」作為最後一行。"
        ),
        "expected_completion_status": "complete",
        "must_include": ("1. 問題：", "2. 可能原因：", "3. 結論：已完成三步驟回答"),
        "must_not_include": ("##", "**", "- ", "4.", "我會", "稍後", "正在", "請稍候"),
        "must_end_with": "3. 結論：已完成三步驟回答",
        "expected_non_empty_lines": 3,
        "require_no_tool_error": True,
        "require_run_trace": True,
        "max_response_chars": 500,
    },
    {
        "id": "exact_two_line_output",
        "label": "Exact two-line output",
        "prompt": "請只輸出以下兩行，不要加入其他文字：\n狀態：完成\n代碼：A7-42",
        "expected_completion_status": "complete",
        "exact_response": "狀態：完成\n代碼：A7-42",
        "must_not_include": ("```", "以下", "說明", "我會", "稍後", "正在", "請稍候"),
        "expected_non_empty_lines": 2,
        "require_no_tool_error": True,
        "require_run_trace": True,
        "max_response_chars": 80,
    },
    {
        "id": "exact_json_output",
        "label": "Exact JSON output",
        "prompt": (
            "請只輸出這一行 JSON，不要使用 Markdown code fence，也不要新增欄位："
            '{"status":"complete","items":["alpha","beta"]}'
        ),
        "expected_completion_status": "complete",
        "exact_response": '{"status":"complete","items":["alpha","beta"]}',
        "must_not_include": ("```", "json", "以下", "說明", "我會", "稍後", "正在", "請稍候"),
        "expected_non_empty_lines": 1,
        "require_no_tool_error": True,
        "require_run_trace": True,
        "max_response_chars": 100,
    },
)


def run_task_completion_smoke() -> dict[str, Any]:
    """Run deterministic task-completion smoke cases without calling an LLM."""
    cases = [evaluate_task_completion_case(case, case.get("sample_result")) for case in TASK_COMPLETION_SMOKE_CASES]
    return _summarize_cases(cases, live=False)


async def run_live_task_completion_eval(
    *,
    agent: Any,
    storage: Any,
    channel: str = "web",
    timeout_seconds: float = 45.0,
    model_info: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run fixed task-completion cases against the active agent."""
    cases = []
    model = _model_info_payload(model_info)
    batch_id = f"eval_batch_{uuid4().hex}"
    for case in TASK_COMPLETION_LIVE_CASES:
        cases.append(
            await _run_live_task_completion_case(
                case,
                agent=agent,
                storage=storage,
                channel=channel,
                timeout_seconds=timeout_seconds,
                model_info=model,
                batch_id=batch_id,
            )
        )
        stored = await _persist_eval_case(storage, cases[-1])
        if stored is not None:
            cases[-1]["eval_id"] = stored.eval_id
    return _summarize_cases(cases, live=True, model_info=model, batch_id=batch_id)


def _summarize_cases(
    cases: list[dict[str, Any]],
    *,
    live: bool,
    model_info: Mapping[str, Any] | None = None,
    batch_id: str = "",
) -> dict[str, Any]:
    total_checks = sum(len(case["checks"]) for case in cases)
    passed_checks = sum(1 for case in cases for check in case["checks"] if check["ok"])
    passed_cases = sum(1 for case in cases if case["ok"])

    return {
        "ok": all(case["ok"] for case in cases),
        "live": live,
        "model": _model_info_payload(model_info),
        "batch_id": _string(batch_id),
        "cases": cases,
        "summary": {
            "passed_cases": passed_cases,
            "total_cases": len(cases),
            "passed_checks": passed_checks,
            "total_checks": total_checks,
        },
    }


async def _persist_eval_case(storage: Any, evaluated_case: Mapping[str, Any]) -> StoredEvalRun | None:
    add_eval_run = getattr(storage, "add_eval_run", None)
    if not callable(add_eval_run):
        return None
    stored = StoredEvalRun(
        eval_id=f"eval_{uuid4().hex}",
        kind="task_completion",
        case_id=_string(evaluated_case.get("id")),
        ok=_bool(evaluated_case.get("ok")),
        summary={
            "text": _string(evaluated_case.get("summary")),
            "score": dict(evaluated_case.get("score") or {}),
        },
        checks=[dict(check) for check in evaluated_case.get("checks", []) if isinstance(check, Mapping)],
        prompt=_string(evaluated_case.get("prompt")),
        response_preview=_string(evaluated_case.get("response_preview")),
        session_id=_string(evaluated_case.get("session_id")),
        run_id=_string(evaluated_case.get("run_id")),
        completion_status=_string(evaluated_case.get("completion_status")),
        had_tool_error=_bool(evaluated_case.get("had_tool_error")),
        metadata={
            "live": _bool(evaluated_case.get("live")),
            "case_label": _string(evaluated_case.get("label")),
            "run_status": _string(evaluated_case.get("run_status")),
            "error": _string(evaluated_case.get("error")),
            "model": _model_info_payload(evaluated_case.get("model")),
            "batch_id": _string(evaluated_case.get("batch_id")),
            "expected_summary": _string(evaluated_case.get("expected_summary")),
            "actual_response": _string(evaluated_case.get("actual_response")),
            "response_source": _string(evaluated_case.get("response_source")),
        },
        created_at=time.time(),
    )
    return await add_eval_run(stored)


def evaluate_task_completion_case(case: Mapping[str, Any], result: Mapping[str, Any] | None) -> dict[str, Any]:
    """Evaluate one task-completion case against a captured run result."""
    result = result or {}
    case_id = _string(case.get("id"), default="case")
    response_text = _string(result.get("response_text"))
    completion_status = _string(result.get("completion_status") or result.get("status")).lower()
    expected_status = _string(case.get("expected_completion_status")).lower()
    had_tool_error = _bool(result.get("had_tool_error"))
    require_no_tool_error = _bool(case.get("require_no_tool_error", True))

    checks: list[dict[str, Any]] = []
    checks.append(
        _check(
            "response_present",
            "Final response is present",
            bool(response_text.strip()),
            f"Observed {len(response_text.strip())} response character(s).",
        )
    )

    if expected_status:
        checks.append(
            _check(
                "completion_status",
                "Completion gate status matches",
                completion_status == expected_status,
                f"Expected {expected_status or '-'}, observed {completion_status or '-'}.",
            )
        )

    if require_no_tool_error:
        checks.append(
            _check(
                "tool_errors",
                "No tool error was reported",
                not had_tool_error,
                "Tool errors were reported." if had_tool_error else "No tool errors reported.",
            )
        )

    if _bool(case.get("require_run_trace", False)):
        run_id = _string(result.get("run_id"))
        checks.append(
            _check(
                "run_trace",
                "Run trace is available",
                bool(run_id),
                f"Observed run {run_id}." if run_id else "Run trace was missing.",
            )
        )

    max_response_chars = _optional_int(case.get("max_response_chars"))
    if max_response_chars is not None:
        response_len = len(response_text.strip())
        checks.append(
            _check(
                "max_response_chars",
                f"Response is at most {max_response_chars} characters",
                response_len <= max_response_chars,
                f"Observed {response_len} response character(s).",
            )
        )

    expected_line_count = _optional_int(case.get("expected_non_empty_lines"))
    if expected_line_count is not None:
        observed_line_count = _non_empty_line_count(response_text)
        checks.append(
            _check(
                "expected_non_empty_lines",
                f"Response has {expected_line_count} non-empty line(s)",
                observed_line_count == expected_line_count,
                f"Expected {expected_line_count}, observed {observed_line_count}.",
            )
        )

    exact_response = _string(case.get("exact_response"))
    if exact_response:
        exact_match = _exact_response_matches(response_text, exact_response)
        checks.append(
            _check(
                "exact_response",
                "Response exactly matches the expected text",
                exact_match,
                "Exact response matched." if exact_match else "Exact response did not match.",
            )
        )

    must_end_with = _string(case.get("must_end_with"))
    if must_end_with:
        ends_with = _ends_with(response_text, must_end_with)
        checks.append(
            _check(
                f"must_end_with_{_slug(must_end_with)}",
                f"Response ends with `{must_end_with}`",
                ends_with,
                "Required ending was found." if ends_with else "Required ending was missing.",
            )
        )

    for phrase in _string_sequence(case.get("must_include")):
        found = _contains(response_text, phrase)
        checks.append(
            _check(
                f"must_include_{_slug(phrase)}",
                f"Response includes `{phrase}`",
                found,
                "Required phrase was found." if found else "Required phrase was missing.",
            )
        )

    for phrase in _string_sequence(case.get("must_not_include")):
        found = _contains(response_text, phrase)
        checks.append(
            _check(
                f"must_not_include_{_slug(phrase)}",
                f"Response excludes `{phrase}`",
                not found,
                "Forbidden phrase was found." if found else "Forbidden phrase was absent.",
            )
        )

    passed_checks = sum(1 for check in checks if check["ok"])
    return {
        "id": case_id,
        "label": _string(case.get("label"), default=case_id),
        "prompt": _string(case.get("prompt")),
        "ok": passed_checks == len(checks),
        "score": {"passed": passed_checks, "total": len(checks)},
        "summary": f"{passed_checks}/{len(checks)} checks passed.",
        "completion_status": completion_status,
        "had_tool_error": had_tool_error,
        "session_id": _string(result.get("session_id")),
        "run_id": _string(result.get("run_id")),
        "run_status": _string(result.get("run_status")),
        "error": _string(result.get("error")),
        "model": _model_info_payload(result.get("model")),
        "response_source": _string(result.get("response_source")),
        "expected_summary": _expected_summary(case),
        "actual_response": response_text.strip(),
        "response_preview": _preview(response_text),
        "checks": checks,
    }


async def _run_live_task_completion_case(
    case: Mapping[str, Any],
    *,
    agent: Any,
    storage: Any,
    channel: str,
    timeout_seconds: float,
    model_info: Mapping[str, Any],
    batch_id: str,
) -> dict[str, Any]:
    case_id = _string(case.get("id"), default="case")
    external_chat_id = f"eval-task-completion-{case_id}-{uuid4().hex[:12]}"
    session_id = f"{channel}:{external_chat_id}"
    response_text = ""
    error = ""

    try:
        response = await asyncio.wait_for(
            agent.process(
                UserMessage(
                    text=_string(case.get("prompt")),
                    channel=channel,
                    external_chat_id=external_chat_id,
                    session_id=session_id,
                    sender_id="task-completion-eval",
                    sender_name="Task completion eval",
                    metadata={
                        "eval_kind": "task_completion",
                        "eval_case_id": case_id,
                        "eval_batch_id": batch_id,
                        "eval_model": dict(model_info),
                    },
                )
            ),
            timeout=max(1.0, float(timeout_seconds or 1.0)),
        )
        response_text = _string(getattr(response, "text", ""))
    except asyncio.TimeoutError:
        error = f"Timed out after {timeout_seconds:.0f} seconds."
    except Exception as exc:  # pragma: no cover - exercised through integration error paths.
        error = f"{type(exc).__name__}: {exc}"

    result = await _live_result_from_storage(
        storage,
        session_id=session_id,
        response_text=response_text,
        error=error,
        model_info=model_info,
    )
    evaluated = evaluate_task_completion_case(case, result)
    evaluated["live"] = True
    evaluated["batch_id"] = batch_id
    return evaluated


async def _live_result_from_storage(
    storage: Any,
    *,
    session_id: str,
    response_text: str,
    error: str,
    model_info: Mapping[str, Any],
) -> dict[str, Any]:
    run = None
    trace = None
    get_latest_run = getattr(storage, "get_latest_run", None)
    if callable(get_latest_run):
        run = await get_latest_run(session_id)
    if run is None:
        runs = await storage.get_runs(session_id, limit=1)
        run = runs[0] if runs else None
    if run is not None:
        get_run_trace = getattr(storage, "get_run_trace", None)
        trace = await get_run_trace(session_id, run.run_id) if callable(get_run_trace) else None

    events = list(getattr(trace, "events", []) or [])
    completion_payload = _latest_event_payload(events, "completion_gate.evaluated") or {}
    terminal_payload = (
        _latest_event_payload(events, RUN_FINISHED_EVENT)
        or _latest_event_payload(events, RUN_FAILED_EVENT)
        or _latest_event_payload(events, RUN_CANCELLED_EVENT)
        or {}
    )
    run_metadata = dict(getattr(run, "metadata", {}) or {}) if run is not None else {}
    resolved_response_text, response_source = await _resolve_live_response_text(
        storage,
        session_id=session_id,
        trace=trace,
        response_text=response_text,
    )
    return {
        "session_id": session_id,
        "run_id": getattr(run, "run_id", "") if run is not None else "",
        "run_status": getattr(run, "status", "") if run is not None else "",
        "response_text": resolved_response_text,
        "response_source": response_source,
        "completion_status": completion_payload.get("status") or "",
        "had_tool_error": _bool(run_metadata.get("had_tool_error")) or _bool(terminal_payload.get("had_tool_error")),
        "error": error,
        "model": dict(model_info),
    }


async def _resolve_live_response_text(
    storage: Any,
    *,
    session_id: str,
    trace: Any,
    response_text: str,
) -> tuple[str, str]:
    direct_response = _string(response_text)
    if direct_response:
        return direct_response, "agent_return"

    for part in reversed(list(getattr(trace, "parts", []) or [])):
        if getattr(part, "part_type", "") != "assistant_message":
            continue
        part_content = _string(getattr(part, "content", ""))
        if part_content:
            return part_content, "run_part"

    get_messages = getattr(storage, "get_messages", None)
    if callable(get_messages):
        try:
            messages = await get_messages(session_id, limit=10)
        except TypeError:
            messages = await get_messages(session_id)
        for message in reversed(list(messages or [])):
            role = _string(getattr(message, "role", "") if not isinstance(message, Mapping) else message.get("role"))
            if role != "assistant":
                continue
            content = _string(getattr(message, "content", "") if not isinstance(message, Mapping) else message.get("content"))
            if content:
                return content, "stored_message"

    return direct_response, ""


def _latest_event_payload(events: Sequence[Any], event_type: str) -> dict[str, Any] | None:
    for event in reversed(events):
        if getattr(event, "event_type", None) == event_type:
            payload = getattr(event, "payload", None)
            return dict(payload) if isinstance(payload, Mapping) else {}
    return None


def _check(check_id: str, label: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "label": label, "ok": bool(ok), "detail": detail}


def _contains(text: str, phrase: str) -> bool:
    return _normalize(phrase) in _normalize(text)


def _ends_with(text: str, phrase: str) -> bool:
    return _normalize(text).endswith(_normalize(phrase))


def _exact_response_matches(text: str, expected: str) -> bool:
    return _normalize_line_endings(text).strip() == _normalize_line_endings(expected).strip()


def _non_empty_line_count(text: str) -> int:
    return len([line for line in _normalize_line_endings(text).split("\n") if line.strip()])


def _normalize_line_endings(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _preview(text: str) -> str:
    value = str(text or "").strip()
    if len(value) <= _RESPONSE_PREVIEW_CHARS:
        return value
    return f"{value[: _RESPONSE_PREVIEW_CHARS - 1]}..."


def _expected_summary(case: Mapping[str, Any]) -> str:
    exact_response = _string(case.get("exact_response"))
    if exact_response:
        return exact_response

    parts: list[str] = []
    expected_status = _string(case.get("expected_completion_status"))
    if expected_status:
        parts.append(f"Completion status: {expected_status}")
    expected_lines = _optional_int(case.get("expected_non_empty_lines"))
    if expected_lines is not None:
        parts.append(f"Non-empty lines: {expected_lines}")
    must_end_with = _string(case.get("must_end_with"))
    if must_end_with:
        parts.append(f"Must end with: {must_end_with}")
    must_include = _string_sequence(case.get("must_include"))
    if must_include:
        parts.append(f"Must include: {', '.join(must_include)}")
    must_not_include = _string_sequence(case.get("must_not_include"))
    if must_not_include:
        parts.append(f"Must not include: {', '.join(must_not_include)}")
    max_response_chars = _optional_int(case.get("max_response_chars"))
    if max_response_chars is not None:
        parts.append(f"Max response chars: {max_response_chars}")
    if _bool(case.get("require_no_tool_error", True)):
        parts.append("No tool errors")
    if _bool(case.get("require_run_trace", False)):
        parts.append("Run trace required")
    return "; ".join(parts)


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", _normalize(text)).strip("_")
    return value or "phrase"


def _string(value: Any, *, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _model_info_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: value[key]
        for key in ("provider_id", "provider", "model", "configured", "context_window_tokens")
        if key in value and value[key] not in (None, "")
    }


def _string_sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (_string(value),) if _string(value) else ()
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(text for text in (_string(item) for item in value) if text)
    return ()


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


__all__ = [
    "TASK_COMPLETION_LIVE_CASES",
    "TASK_COMPLETION_SMOKE_CASES",
    "evaluate_task_completion_case",
    "run_live_task_completion_eval",
    "run_task_completion_smoke",
]
