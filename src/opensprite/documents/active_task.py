"""Per-chat ACTIVE_TASK.md store and consolidator."""

from __future__ import annotations

import re
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..config.schema import DocumentLlmConfig
from ..context.paths import get_active_task_event_log_file, get_active_task_file, get_active_task_state_file
from ..storage import StoredMessage, StorageProvider
from ..storage.base import get_storage_message_count, get_storage_messages_slice
from ..utils import count_text_tokens
from ..utils.log import logger
from .base import ConversationConsolidator, ConversationDocumentStore
from .managed import ManagedMarkdownDocument
from .state import JsonProgressStore

ACTIVE_TASK_HEADER = "## Task State"
ACTIVE_TASK_START_MARKER = "<!-- OPENSPRITE:ACTIVE_TASK:START -->"
ACTIVE_TASK_END_MARKER = "<!-- OPENSPRITE:ACTIVE_TASK:END -->"
TASK_BOUNDARY_CONFIRMATION_EVENT = "task_boundary_confirmation"
TASK_BOUNDARY_CONFIRMATION_RESOLVED_EVENT = "task_boundary_confirmation_resolved"
DEFAULT_ACTIVE_TASK_CONTENT = """- Status: inactive
- Goal: not set
- Deliverable: not set
- Definition of done:
  - not set
- Constraints:
  - none
- Assumptions:
  - none
- Plan:
  1. not set
- Current step: not set
- Next step: not set
- Completed steps:
  - none
- Open questions:
  - none"""
_ACTIVE_TASK_BOOTSTRAP = """# ACTIVE_TASK.md - Current Task Contract

This file stores the active multi-step task for this session.
It should stay concise, execution-oriented, and current.
If there is no active task, keep the status as `inactive`.

## Task State

This section is maintained by OpenSprite.

<!-- OPENSPRITE:ACTIVE_TASK:START -->
- Status: inactive
- Goal: not set
- Deliverable: not set
- Definition of done:
  - not set
- Constraints:
  - none
- Assumptions:
  - none
- Plan:
  1. not set
- Current step: not set
- Next step: not set
- Completed steps:
  - none
- Open questions:
  - none
<!-- OPENSPRITE:ACTIVE_TASK:END -->
"""
_ACTIVE_STATUS_RE = re.compile(r"^- Status:\s*(?P<status>.+)$", re.MULTILINE)
INACTIVE_ACTIVE_TASK_STATUS = "inactive"
ACTIVE_ACTIVE_TASK_STATUS = "active"
BLOCKED_ACTIVE_TASK_STATUS = "blocked"
WAITING_USER_ACTIVE_TASK_STATUS = "waiting_user"
DONE_ACTIVE_TASK_STATUS = "done"
CANCELLED_ACTIVE_TASK_STATUS = "cancelled"
WAITING_USER_ACTIVE_TASK_DEFAULT_OPEN_QUESTION = "need user input"
BLOCKED_ACTIVE_TASK_DEFAULT_OPEN_QUESTION = "blocked"
OPEN_QUESTIONS_CLEAR_SENTINEL = "none"
CURRENT_ACTIVE_TASK_STATUSES = frozenset(
    {ACTIVE_ACTIVE_TASK_STATUS, BLOCKED_ACTIVE_TASK_STATUS, WAITING_USER_ACTIVE_TASK_STATUS}
)
CURRENT_OR_DONE_ACTIVE_TASK_STATUSES = CURRENT_ACTIVE_TASK_STATUSES | frozenset({DONE_ACTIVE_TASK_STATUS})
TERMINAL_ACTIVE_TASK_STATUSES = frozenset({DONE_ACTIVE_TASK_STATUS, CANCELLED_ACTIVE_TASK_STATUS})
OPEN_QUESTION_CLEAR_ACTIVE_TASK_STATUSES = frozenset(
    {ACTIVE_ACTIVE_TASK_STATUS, DONE_ACTIVE_TASK_STATUS, CANCELLED_ACTIVE_TASK_STATUS}
)
_ACTIVE_STATUSES_TO_INCLUDE = CURRENT_ACTIVE_TASK_STATUSES
_ALLOWED_ACTIVE_TASK_STATUSES = {
    INACTIVE_ACTIVE_TASK_STATUS,
    ACTIVE_ACTIVE_TASK_STATUS,
    BLOCKED_ACTIVE_TASK_STATUS,
    WAITING_USER_ACTIVE_TASK_STATUS,
    DONE_ACTIVE_TASK_STATUS,
    CANCELLED_ACTIVE_TASK_STATUS,
}
_INACTIVE_OR_TERMINAL_STATUSES = {
    INACTIVE_ACTIVE_TASK_STATUS,
    DONE_ACTIVE_TASK_STATUS,
    CANCELLED_ACTIVE_TASK_STATUS,
}
_DEFAULT_ACTIVE_TASK_DELIVERABLE = "a concrete result aligned with the user's request"
_AUTO_ALLOWED_STATUS_TRANSITIONS = {
    INACTIVE_ACTIVE_TASK_STATUS: {
        INACTIVE_ACTIVE_TASK_STATUS,
        ACTIVE_ACTIVE_TASK_STATUS,
        BLOCKED_ACTIVE_TASK_STATUS,
        WAITING_USER_ACTIVE_TASK_STATUS,
        DONE_ACTIVE_TASK_STATUS,
    },
    ACTIVE_ACTIVE_TASK_STATUS: {
        ACTIVE_ACTIVE_TASK_STATUS,
        BLOCKED_ACTIVE_TASK_STATUS,
        WAITING_USER_ACTIVE_TASK_STATUS,
        DONE_ACTIVE_TASK_STATUS,
    },
    BLOCKED_ACTIVE_TASK_STATUS: {
        BLOCKED_ACTIVE_TASK_STATUS,
        ACTIVE_ACTIVE_TASK_STATUS,
        WAITING_USER_ACTIVE_TASK_STATUS,
        DONE_ACTIVE_TASK_STATUS,
    },
    WAITING_USER_ACTIVE_TASK_STATUS: {
        WAITING_USER_ACTIVE_TASK_STATUS,
        ACTIVE_ACTIVE_TASK_STATUS,
        BLOCKED_ACTIVE_TASK_STATUS,
        DONE_ACTIVE_TASK_STATUS,
    },
    DONE_ACTIVE_TASK_STATUS: {DONE_ACTIVE_TASK_STATUS},
    CANCELLED_ACTIVE_TASK_STATUS: {CANCELLED_ACTIVE_TASK_STATUS},
}


def active_task_status(active_task_snapshot: str | None) -> str:
    """Return the normalized status from a rendered ACTIVE_TASK block."""
    match = _ACTIVE_STATUS_RE.search(str(active_task_snapshot or ""))
    if not match:
        return INACTIVE_ACTIVE_TASK_STATUS
    return match.group("status").strip().lower() or INACTIVE_ACTIVE_TASK_STATUS


def has_current_active_task(active_task_snapshot: str | None) -> bool:
    """Return whether the rendered ACTIVE_TASK block represents current work."""
    return is_current_active_task_status(active_task_status(active_task_snapshot))


def is_current_active_task_status(status: str | None) -> bool:
    """Return whether a stored ACTIVE_TASK status represents current work."""
    return str(status or "").strip().lower() in CURRENT_ACTIVE_TASK_STATUSES


def is_current_or_done_active_task_status(status: str | None) -> bool:
    """Return whether a stored ACTIVE_TASK status can be mirrored from work progress."""
    return str(status or "").strip().lower() in CURRENT_OR_DONE_ACTIVE_TASK_STATUSES


def is_inactive_active_task_status(status: str | None) -> bool:
    """Return whether a stored ACTIVE_TASK status is inactive."""
    return str(status or "").strip().lower() == INACTIVE_ACTIVE_TASK_STATUS


def is_terminal_active_task_status(status: str | None) -> bool:
    """Return whether a stored ACTIVE_TASK status is terminal and resumable."""
    return str(status or "").strip().lower() in TERMINAL_ACTIVE_TASK_STATUSES


def clears_active_task_open_questions(status: str | None) -> bool:
    """Return whether open questions should be cleared for this status."""
    return str(status or "").strip().lower() in OPEN_QUESTION_CLEAR_ACTIVE_TASK_STATUSES


def clear_open_questions() -> list[str]:
    return [OPEN_QUESTIONS_CLEAR_SENTINEL]


def normalize_open_questions(values: list[object] | tuple[object, ...] | None) -> list[str] | None:
    if values is None:
        return None
    questions = [str(item).strip() for item in values if str(item).strip()]
    if any(item.lower() == OPEN_QUESTIONS_CLEAR_SENTINEL for item in questions):
        return clear_open_questions()
    return questions


class ActiveTaskStore(ConversationDocumentStore):
    """Persist one session's ACTIVE_TASK.md and its update state."""

    def __init__(self, active_task_file: Path, state_file: Path, event_log_file: Path):
        self.active_task_file = Path(active_task_file).expanduser()
        self.state = JsonProgressStore(state_file)
        self.event_log_file = Path(event_log_file).expanduser()
        self.event_log_file.parent.mkdir(parents=True, exist_ok=True)
        self.document = ManagedMarkdownDocument(
            self.active_task_file,
            start_marker=ACTIVE_TASK_START_MARKER,
            end_marker=ACTIVE_TASK_END_MARKER,
            default_content=DEFAULT_ACTIVE_TASK_CONTENT,
            heading=ACTIVE_TASK_HEADER,
            intro="This section is maintained by OpenSprite.",
            anchor_heading=None,
            bootstrap_text=_ACTIVE_TASK_BOOTSTRAP,
        )

    def read(self, session_id: str) -> str:
        return self.document.read_text()

    def read_text(self) -> str:
        return self.document.read_text()

    def write(self, session_id: str, content: str) -> None:
        self.document.write_managed_block(content)

    def read_managed_block(self) -> str:
        return self.document.read_managed_block()

    def write_managed_block(self, content: str) -> None:
        self.document.write_managed_block(content)

    def get_processed_index(self, session_id: str) -> int:
        return self.state.get_processed_index(session_id)

    def set_processed_index(self, session_id: str, index: int) -> None:
        self.state.set_processed_index(session_id, index)

    def clear(self, session_id: str) -> None:
        self.document.write_managed_block(DEFAULT_ACTIVE_TASK_CONTENT)
        self.state.set_processed_index(session_id, 0)

    def read_status(self) -> str:
        return active_task_status(self.read_managed_block())

    def get_context(self, session_id: str) -> str:
        status = self.read_status()
        if status not in _ACTIVE_STATUSES_TO_INCLUDE:
            return ""
        return f"# Active Task\n\n{self.read_managed_block()}"

    def render_for_user(self) -> str | None:
        status = self.read_status()
        if status == "inactive":
            return None
        block = self.read_managed_block()
        goal = _extract_task_field(block, "Goal")
        deliverable = _extract_task_field(block, "Deliverable")
        current_step = _extract_task_field(block, "Current step")
        next_step = _extract_task_field(block, "Next step")
        open_questions = [entry for entry in _extract_indented_section(block, "Open questions") if entry.lower() != "none"]

        lines = [
            "# Active Task",
            "",
            f"- Status: {status}",
            f"- Goal: {goal}",
        ]
        if deliverable != "not set":
            lines.append(f"- Deliverable: {deliverable}")
        if current_step != "not set":
            lines.append(f"- Current step: {current_step}")
        if next_step != "not set":
            lines.append(f"- Next step: {next_step}")
        if open_questions:
            lines.append(f"- Open question: {open_questions[0]}")
        return "\n".join(lines)

    def render_full_for_user(self) -> str | None:
        status = self.read_status()
        if status == "inactive":
            return None
        return f"# Active Task\n\n{self.read_managed_block()}"

    def set_status(self, status: str) -> str:
        updated = normalize_active_task_block(
            _replace_scalar_field(self.read_managed_block(), "Status", status),
            previous_block=self.read_managed_block(),
        )
        self.write_managed_block(updated)
        return updated

    def append_event(self, event_type: str, source: str, *, details: dict[str, Any] | None = None) -> None:
        block = self.read_managed_block()
        event = {
            "timestamp": time.time(),
            "event_type": event_type,
            "source": source,
            "status": self.read_status(),
            "goal": _extract_task_field(block, "Goal"),
            "current_step": _extract_task_field(block, "Current step"),
            "next_step": _extract_task_field(block, "Next step"),
            "details": dict(details or {}),
        }
        with self.event_log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_events(self, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.event_log_file.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.event_log_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        if limit is not None:
            return events[-limit:]
        return events

    def read_pending_boundary_request(self) -> str | None:
        """Return the unresolved structured task-boundary request, if one exists."""
        if self.read_status() != "waiting_user":
            return None
        for event in reversed(self.read_events()):
            event_type = str(event.get("event_type") or "")
            if event_type == TASK_BOUNDARY_CONFIRMATION_RESOLVED_EVENT:
                return None
            if event_type != TASK_BOUNDARY_CONFIRMATION_EVENT:
                continue
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            pending_request = str(details.get("pending_request") or "").strip()
            return pending_request or None
        return None

    def render_history(self, limit: int = 10) -> str | None:
        events = self.read_events(limit=limit)
        if not events:
            return None
        lines = ["# Active Task History", ""]
        for event in events:
            timestamp = datetime.fromtimestamp(float(event.get("timestamp", 0) or 0)).strftime("%Y-%m-%d %H:%M:%S")
            event_type = str(event.get("event_type", "update") or "update")
            source = str(event.get("source", "system") or "system")
            source_label = {
                "user": "manual",
                "auto": "auto",
                "immediate": "immediate",
            }.get(source, source)
            status = str(event.get("status", "inactive") or "inactive")
            current_step = str(event.get("current_step", "not set") or "not set")
            next_step = str(event.get("next_step", "not set") or "not set")
            lines.append(f"- [{timestamp}] {event_type} ({source_label})")
            lines.append(f"  - status: {status}")
            lines.append(f"  - current step: {current_step}")
            lines.append(f"  - next step: {next_step}")
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            for key, value in details.items():
                lines.append(f"  - {key}: {value}")
        return "\n".join(lines)

    def update_fields(
        self,
        *,
        status: str | None = None,
        current_step: str | None = None,
        next_step: str | None = None,
        open_questions: list[str] | None = None,
        append_completed_step: str | None = None,
        force: bool = False,
    ) -> str:
        previous = self.read_managed_block()
        updated = previous
        if status is not None:
            updated = _replace_scalar_field(updated, "Status", status)
        if current_step is not None:
            updated = _replace_scalar_field(updated, "Current step", current_step)
        if next_step is not None:
            updated = _replace_scalar_field(updated, "Next step", next_step)
        if open_questions is not None:
            cleaned_questions = [item.strip() for item in open_questions if item and item.strip()]
            updated = _replace_indented_section(updated, "Open questions", cleaned_questions or ["none"])
        if append_completed_step is not None:
            item = append_completed_step.strip()
            if item and item.lower() != "none":
                completed = [entry for entry in _extract_indented_section(updated, "Completed steps") if entry.lower() != "none"]
                if item not in completed:
                    completed.append(item)
                updated = _replace_indented_section(updated, "Completed steps", completed or ["none"])

        normalized = normalize_active_task_block(
            updated,
            previous_block=previous,
            allow_terminal_override=force,
        )
        self.write_managed_block(normalized)
        return normalized

    def complete_current_step(self, *, next_step_override: str | None = None) -> str | None:
        """Mark the current step completed and advance or finish the task."""
        previous = self.read_managed_block()
        current_step = _extract_task_field(previous, "Current step")
        next_step = next_step_override or _extract_task_field(previous, "Next step")
        if current_step == "not set":
            return None

        completed = [entry for entry in _extract_indented_section(previous, "Completed steps") if entry.lower() != "none"]
        if current_step not in completed:
            completed.append(current_step)

        updated = _replace_indented_section(previous, "Completed steps", completed or ["none"])
        if next_step and next_step != "not set":
            updated = _replace_scalar_field(updated, "Status", "active")
            updated = _replace_scalar_field(updated, "Current step", next_step)
            updated = _replace_scalar_field(updated, "Next step", "not set")
            updated = _replace_indented_section(updated, "Open questions", ["none"])
        else:
            updated = _replace_scalar_field(updated, "Status", "done")

        normalized = normalize_active_task_block(updated, previous_block=previous, allow_terminal_override=True)
        self.write_managed_block(normalized)
        return normalized


def _normalize_goal_text(text: str, max_chars: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def build_task_block_from_intent_fields(
    *,
    goal: str,
    definition_of_done: list[str] | tuple[str, ...] | None = None,
    constraints: list[str] | tuple[str, ...] | None = None,
    assumptions: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    """Create an ACTIVE_TASK block from deterministic intent fields."""
    normalized_goal = _normalize_goal_text(goal)
    if not normalized_goal:
        return None

    deliverable = _DEFAULT_ACTIVE_TASK_DELIVERABLE
    done_items = _normalize_task_list(
        definition_of_done,
        default=["the user request is addressed directly", "the result or blocker is explicit"],
    )
    constraint_items = _normalize_task_list(
        constraints,
        default=["preserve user intent", "prefer the smallest correct next step"],
    )
    assumption_items = _normalize_task_list(
        assumptions,
        default=["This task brief was generated from deterministic intent detection and may be refined after more context."],
    )
    plan_items = [
        "inspect the relevant context and refine the task if needed",
        "execute the highest-value next step toward the goal",
        "verify the result or state the blocking gap",
    ]

    return (
        "- Status: active\n"
        f"- Goal: {normalized_goal}\n"
        f"- Deliverable: {deliverable}\n"
        "- Definition of done:\n"
        f"{_format_bulleted_task_list(done_items)}\n"
        "- Constraints:\n"
        f"{_format_bulleted_task_list(constraint_items)}\n"
        "- Assumptions:\n"
        f"{_format_bulleted_task_list(assumption_items)}\n"
        "- Plan:\n"
        f"{_format_numbered_task_list(plan_items)}\n"
        f"- Current step: 1. {plan_items[0]}\n"
        f"- Next step: 2. {plan_items[1]}\n"
        "- Completed steps:\n"
        "  - none\n"
        "- Open questions:\n"
        "  - none"
    )


def build_task_block_from_text(message_text: str, *, force: bool = False) -> str | None:
    """Create a minimal task brief from free-form text."""
    stripped = (message_text or "").strip()
    if not stripped:
        return None
    if stripped.startswith("/"):
        return None
    if not force:
        return None

    goal = _normalize_goal_text(stripped)

    deliverable = _DEFAULT_ACTIVE_TASK_DELIVERABLE
    assumptions = (
        "The initial request is brief and this task brief may be refined after more context."
        if len(goal.split()) <= 12
        else "This initial task brief was generated from the latest user request and may be refined after more context."
    )
    return (
        "- Status: active\n"
        f"- Goal: {goal}\n"
        f"- Deliverable: {deliverable}\n"
        "- Definition of done:\n"
        "  - the user request is addressed directly\n"
        "  - the result or blocker is explicit\n"
        "- Constraints:\n"
        "  - preserve user intent\n"
        "  - prefer the smallest correct next step\n"
        "- Assumptions:\n"
        f"  - {assumptions}\n"
        "- Plan:\n"
        "  1. inspect the relevant context and refine the task if needed\n"
        "  2. execute the highest-value next step toward the goal\n"
        "  3. verify the result or state the blocking gap\n"
        "- Current step: 1. inspect the relevant context and refine the task if needed\n"
        "- Next step: 2. execute the highest-value next step toward the goal\n"
        "- Completed steps:\n"
        "  - none\n"
        "- Open questions:\n"
        "  - none"
    )


def _normalize_task_list(
    values: list[str] | tuple[str, ...] | None,
    *,
    default: list[str],
    max_items: int = 5,
) -> list[str]:
    items: list[str] = []
    for value in values or []:
        compact = _normalize_goal_text(str(value or ""), max_chars=160)
        if not compact:
            continue
        if compact.lower() in {"none", "not set"}:
            continue
        items.append(compact)
    return list(dict.fromkeys(items[:max_items])) or list(default)


def _format_bulleted_task_list(items: list[str]) -> str:
    return "\n".join(f"  - {item}" for item in items)


def _format_numbered_task_list(items: list[str]) -> str:
    return "\n".join(f"  {index}. {item}" for index, item in enumerate(items, start=1))


def _extract_task_field(task_block: str, field_name: str) -> str:
    match = re.search(rf"^- {re.escape(field_name)}:\s*(.+)$", task_block, re.MULTILINE)
    if not match:
        return "not set"
    return match.group(1).strip() or "not set"


def _replace_scalar_field(task_block: str, field_name: str, value: str) -> str:
    pattern = rf"^- {re.escape(field_name)}:\s*.*$"
    replacement = f"- {field_name}: {value}"
    return re.sub(pattern, lambda _match: replacement, task_block, count=1, flags=re.MULTILINE)


def _replace_indented_section(task_block: str, field_name: str, lines: list[str]) -> str:
    section_body = "\n".join(f"  - {line}" for line in lines)
    replacement = f"- {field_name}:\n{section_body}"
    pattern = rf"^- {re.escape(field_name)}:\n(?:  .*\n?)*"
    return re.sub(pattern, lambda _match: replacement, task_block, count=1, flags=re.MULTILINE)


def _extract_indented_section(task_block: str, field_name: str) -> list[str]:
    pattern = rf"^- {re.escape(field_name)}:\n((?:  .*\n?)*)"
    match = re.search(pattern, task_block, re.MULTILINE)
    if not match:
        return []
    lines: list[str] = []
    for raw_line in match.group(1).splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("- "):
            lines.append(stripped[2:].strip())
        elif stripped:
            lines.append(stripped)
    return lines


def normalize_active_task_block(
    task_block: str,
    previous_block: str | None = None,
    *,
    allow_terminal_override: bool = False,
) -> str:
    """Normalize one ACTIVE_TASK block into a coherent status/step state."""
    normalized = (task_block or "").strip() or DEFAULT_ACTIVE_TASK_CONTENT
    previous_status = _extract_task_field(previous_block or "", "Status").lower()
    status = _extract_task_field(normalized, "Status").lower()

    if status not in _ALLOWED_ACTIVE_TASK_STATUSES:
        status = previous_status if previous_status in _ALLOWED_ACTIVE_TASK_STATUSES else "inactive"
        normalized = _replace_scalar_field(normalized, "Status", status)

    if not allow_terminal_override:
        previous_effective = previous_status if previous_status in _ALLOWED_ACTIVE_TASK_STATUSES else "inactive"
        allowed_transitions = _AUTO_ALLOWED_STATUS_TRANSITIONS.get(previous_effective, {previous_effective})
        if status not in allowed_transitions:
            status = previous_effective
            normalized = _replace_scalar_field(normalized, "Status", status)

    current_step = _extract_task_field(normalized, "Current step")
    next_step = _extract_task_field(normalized, "Next step")

    if status in _INACTIVE_OR_TERMINAL_STATUSES:
        normalized = _replace_scalar_field(normalized, "Current step", "not set")
        normalized = _replace_scalar_field(normalized, "Next step", "not set")
        normalized = _replace_indented_section(normalized, "Open questions", ["none"])
        return normalized

    if status == "active" and current_step == "not set" and next_step != "not set":
        normalized = _replace_scalar_field(normalized, "Current step", next_step)

    if status == "active":
        normalized = _replace_indented_section(normalized, "Open questions", ["none"])

    return normalized


def build_active_task_execution_guidance(task_block: str) -> str:
    """Build a focused execution-discipline section for the current active task."""
    status = _extract_task_field(task_block, "Status").lower()
    current_step = _extract_task_field(task_block, "Current step")
    next_step = _extract_task_field(task_block, "Next step")
    status_rules = ""
    if status == "waiting_user":
        status_rules = (
            "- Because the task is currently `waiting_user`, do not continue execution until the user provides the missing input.\n"
            "- Ask only for the blocking information or decision that unblocks the task.\n"
        )
    elif status == "blocked":
        status_rules = (
            "- Because the task is currently `blocked`, do not pretend progress happened while the blocker remains unresolved.\n"
            "- Explain the blocker clearly and only resume normal execution after it is actually cleared.\n"
        )

    return f"""# Active Task Execution Rules

- Treat the active task as the controlling objective for this session unless the user explicitly switches tasks.
- Current task status: {status}
- Primary focus for this turn: {current_step}
- Planned next step after that: {next_step}
- Do not jump to the planned next step until the current step is clearly completed, blocked, or explicitly replaced.
- Do not mark a step as completed unless the work or evidence in this session clearly shows it was completed.
- If the current step cannot proceed because information is missing, prefer `waiting_user` or `blocked` behavior over pretending progress happened.
- If the user asks a small side question that does not replace the task, answer it briefly and then return to the active task.
- Preserve the current plan unless the user changes the goal or new evidence proves the plan is no longer valid.
{status_rules}"""


def _to_message_dict(message: StoredMessage | dict[str, Any]) -> dict[str, Any]:
    if isinstance(message, dict):
        return {
            "role": message.get("role", "?"),
            "content": message.get("content", ""),
            "tool_name": message.get("tool_name"),
            "metadata": dict(message.get("metadata", {}) or {}),
        }
    return {
        "role": message.role,
        "content": message.content,
        "tool_name": message.tool_name,
        "metadata": dict(message.metadata or {}),
    }


def _summarize_tool_args(tool_name: str | None, metadata: dict[str, Any]) -> str:
    if not tool_name:
        return ""
    tool_args = metadata.get("tool_args")
    if not isinstance(tool_args, dict):
        return ""

    if tool_name == "exec":
        command = str(tool_args.get("command", "") or "").strip()
        if command:
            compact = re.sub(r"\s+", " ", command)
            if len(compact) > 120:
                compact = compact[:117].rstrip() + "..."
            return f" command={compact}"

    for key in ("path", "prompt_type", "skill_name", "server_name", "action", "query", "url"):
        value = tool_args.get(key)
        if value is None:
            continue
        compact = re.sub(r"\s+", " ", str(value).strip())
        if not compact:
            continue
        if len(compact) > 80:
            compact = compact[:77].rstrip() + "..."
        return f" {key}={compact}"

    return ""


def _format_messages(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role", "?")).upper()
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        if len(content) > 1200:
            content = content[:1200] + f"... (truncated from {len(content)} chars)"
        tool_name = str(message.get("tool_name", "") or "").strip()
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        if role == "TOOL" and tool_name:
            detail = _summarize_tool_args(tool_name, metadata)
            lines.append(f"[TOOL:{tool_name.upper()}{detail}] {content}")
        else:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)


async def consolidate_active_task(
    active_task_store: ActiveTaskStore,
    session_id: str,
    messages: list[dict[str, Any]],
    provider,
    model: str,
    *,
    active_task_llm: DocumentLlmConfig,
) -> bool:
    """Update ACTIVE_TASK.md from a recent conversation chunk."""
    if not messages:
        return True

    current_task = active_task_store.read_managed_block()
    transcript = _format_messages(messages)
    if not transcript:
        return True

    prompt = f"""Review this recent conversation chunk and update ACTIVE_TASK.md for the current session.

Current ACTIVE_TASK state:
{current_task}

Conversation chunk:
{transcript}

Rules:
- Keep the exact field order from the required template below.
- Use one of these statuses only: inactive, active, blocked, waiting_user, done, cancelled.
- Use `inactive` when there is no meaningful multi-step task that should stay active.
- Keep Goal and Deliverable to one concise line each.
- Keep Definition of done, Constraints, Assumptions, Completed steps, and Open questions concise bullet lists.
- Keep Plan concise and practical; prefer 3-7 steps when a task is active.
- `Current step` should describe the current step or `not set`.
- `Next step` should describe the single next action or `not set`.
- Keep `Current step` stable until the transcript clearly shows it was completed, blocked, or explicitly replaced.
- Do not advance `Next step` into `Current step` unless the previous current step clearly finished or became impossible to continue.
- Prefer concrete tool evidence, verification output, and command results over assistant self-claims when deciding whether a step completed.
- If a tool result shows tests, checks, validation, or execution output, treat that as stronger evidence than plain assistant narration.
- If a tool result or verification command shows failure, unresolved errors, or missing output, do not mark the step as completed.
- If tool evidence contradicts the assistant's claim of success, trust the tool evidence and keep the task unresolved or blocked.
- Mark steps as completed only when the transcript clearly shows they were completed.
- If the user asked a side question without changing the main task, preserve the task state and do not treat that as a new plan.
- If the task changed materially, update Goal, Deliverable, Plan, and step tracking to match the latest agreed direction.
- If the assistant drifted but the user's task is still clear, restore the task to the user's actual goal instead of preserving the drift.
- Do not copy raw logs or large tool output into ACTIVE_TASK.
- Return markdown only, with no extra commentary.

Required template:
{DEFAULT_ACTIVE_TASK_CONTENT}
"""

    try:
        logger.info(
            "[{}] active_task.prompt | current_chars={} transcript_chars={} messages={}",
            session_id,
            len(current_task),
            len(transcript),
            len(messages),
        )
        llm = active_task_llm
        response = await provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You maintain one session's ACTIVE_TASK.md. Return only the updated markdown block, "
                        "keeping the required structure exactly."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            model=model,
            **llm.decoding_kwargs(),
        )
        update = str(response.content or "").strip()
        if not update:
            logger.warning("Active task consolidation: empty response content")
            return False

        normalized_update = normalize_active_task_block(update, previous_block=current_task)

        if normalized_update != current_task:
            active_task_store.write_managed_block(normalized_update)
            previous_status = _extract_task_field(current_task, "Status")
            new_status = _extract_task_field(normalized_update, "Status")
            previous_current = _extract_task_field(current_task, "Current step")
            new_current = _extract_task_field(normalized_update, "Current step")
            previous_next = _extract_task_field(current_task, "Next step")
            new_next = _extract_task_field(normalized_update, "Next step")
            details: dict[str, Any] = {}
            if previous_status != new_status:
                details["previous_status"] = previous_status
                details["new_status"] = new_status
            if previous_current != new_current:
                details["previous_current_step"] = previous_current
                details["new_current_step"] = new_current
            if previous_next != new_next:
                details["previous_next_step"] = previous_next
                details["new_next_step"] = new_next
            active_task_store.append_event("auto_update", "auto", details=details)
            logger.info(
                "Active task updated for session {}: {} chars ({} tokens)",
                session_id,
                len(normalized_update),
                count_text_tokens(normalized_update, model=model),
            )
        else:
            logger.info("Active task unchanged for session {}", session_id)
        return True
    except Exception as exc:
        logger.error("Active task consolidation failed: {}", exc)
        return False


class ActiveTaskConsolidator(ConversationConsolidator):
    """Manage incremental ACTIVE_TASK.md updates from stored session history."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        provider,
        model: str,
        active_task_store_factory: Callable[[str], ActiveTaskStore],
        threshold: int,
        lookback_messages: int,
        enabled: bool,
        llm: DocumentLlmConfig,
    ):
        self.storage = storage
        self.provider = provider
        self.model = model
        self.active_task_store_factory = active_task_store_factory
        self.threshold = max(1, threshold)
        self.lookback_messages = max(1, lookback_messages)
        self.enabled = enabled
        self.llm = llm

    async def maybe_update(self, session_id: str) -> None:
        if not self.enabled:
            return

        active_task_store = self.active_task_store_factory(session_id)
        message_count = await get_storage_message_count(self.storage, session_id)
        last_processed = active_task_store.get_processed_index(session_id)
        if last_processed > message_count:
            active_task_store.set_processed_index(session_id, message_count)
            return

        pending = message_count - last_processed
        if pending < self.threshold:
            return

        end_index = min(message_count, last_processed + self.lookback_messages)
        chunk = [
            _to_message_dict(message)
            for message in await get_storage_messages_slice(
                self.storage,
                session_id,
                start_index=last_processed,
                end_index=end_index,
            )
        ]
        if not chunk:
            return

        logger.info("[{}] Updating ACTIVE_TASK.md from {} messages", session_id, len(chunk))
        success = await consolidate_active_task(
            active_task_store=active_task_store,
            session_id=session_id,
            messages=chunk,
            provider=self.provider,
            model=self.model,
            active_task_llm=self.llm,
        )
        if success:
            active_task_store.set_processed_index(session_id, end_index)


def create_active_task_store(
    app_home: str | Path | None,
    session_id: str | None,
    *,
    workspace_root: str | Path | None = None,
) -> ActiveTaskStore:
    """Create the per-session ACTIVE_TASK.md store for the given session scope."""
    return ActiveTaskStore(
        active_task_file=get_active_task_file(app_home, session_id=session_id, workspace_root=workspace_root),
        state_file=get_active_task_state_file(app_home, session_id=session_id, workspace_root=workspace_root),
        event_log_file=get_active_task_event_log_file(app_home, session_id=session_id, workspace_root=workspace_root),
    )
