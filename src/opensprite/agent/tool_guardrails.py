"""Pure guardrails for repeated tool-call loops."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..tool_names import (
    BATCH_TOOL_NAME,
    CONFIGURE_MCP_TOOL_NAME,
    CONFIGURE_SKILL_TOOL_NAME,
    CONFIGURE_SUBAGENT_TOOL_NAME,
    CREDENTIAL_STORE_TOOL_NAME,
    CRON_TOOL_NAME,
    DELEGATED_EXECUTION_TOOL_NAMES,
    EXECUTION_TOOL_NAMES,
    READ_SKILL_TOOL_NAME,
    SEND_MEDIA_TOOL_NAME,
    WORKSPACE_WRITE_TOOL_NAMES,
)
from .retrieval import HISTORY_SEARCH_TOOL_NAME
from .tool_groups import WORKSPACE_DISCOVERY_TOOLS
from ..tools.evidence import VERIFICATION_TOOL_NAME
from ..tools.evidence import WEB_SOURCE_EVIDENCE_TOOLS


IDEMPOTENT_TOOL_NAMES = frozenset(
    {
        *WORKSPACE_DISCOVERY_TOOLS,
        BATCH_TOOL_NAME,
        READ_SKILL_TOOL_NAME,
        HISTORY_SEARCH_TOOL_NAME,
        *WEB_SOURCE_EVIDENCE_TOOLS,
    }
)

MUTATING_TOOL_NAMES = frozenset(
    {
        *WORKSPACE_WRITE_TOOL_NAMES,
        *EXECUTION_TOOL_NAMES,
        VERIFICATION_TOOL_NAME,
        *DELEGATED_EXECUTION_TOOL_NAMES,
        "workflow",
        CONFIGURE_SKILL_TOOL_NAME,
        CONFIGURE_SUBAGENT_TOOL_NAME,
        CONFIGURE_MCP_TOOL_NAME,
        CREDENTIAL_STORE_TOOL_NAME,
        CRON_TOOL_NAME,
        SEND_MEDIA_TOOL_NAME,
    }
)


@dataclass(frozen=True)
class ToolLoopGuardrailConfig:
    """Thresholds for one execution loop's repeated tool-call detection."""

    repeated_failure_warn_after: int = 2
    repeated_failure_block_after: int = 3
    same_result_warn_after: int = 2
    same_result_block_after: int = 3
    idempotent_tools: frozenset[str] = field(default_factory=lambda: IDEMPOTENT_TOOL_NAMES)
    mutating_tools: frozenset[str] = field(default_factory=lambda: MUTATING_TOOL_NAMES)


@dataclass(frozen=True)
class ToolCallSignature:
    """Stable non-reversible identity for a tool name plus canonical args."""

    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any] | None) -> "ToolCallSignature":
        return cls(tool_name=tool_name, args_hash=_sha256(_canonical_args(args or {})))

    def to_metadata(self) -> dict[str, str]:
        return {"tool_name": self.tool_name, "args_hash": self.args_hash}


@dataclass(frozen=True)
class ToolLoopGuardrailDecision:
    """Decision returned for a tool-call loop observation."""

    action: str = "allow"  # allow | warn | block
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: ToolCallSignature | None = None

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "warn"}

    def to_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "tool_name": self.tool_name,
            "count": self.count,
        }
        if self.signature is not None:
            payload["signature"] = self.signature.to_metadata()
        return payload


class ToolLoopGuardrail:
    """Track repeated failed or non-progressing tool calls within one run."""

    def __init__(self, config: ToolLoopGuardrailConfig | None = None):
        self.config = config or ToolLoopGuardrailConfig()
        self._failure_counts: dict[ToolCallSignature, int] = {}
        self._same_result_counts: dict[ToolCallSignature, tuple[str, int]] = {}

    def before_call(self, tool_name: str, args: Mapping[str, Any] | None) -> ToolLoopGuardrailDecision:
        signature = ToolCallSignature.from_call(tool_name, _coerce_args(args))
        failure_count = self._failure_counts.get(signature, 0)
        if failure_count >= self.config.repeated_failure_block_after:
            return ToolLoopGuardrailDecision(
                action="block",
                code="repeated_failure_block",
                message=(
                    f"Blocked {tool_name}: the same tool call failed {failure_count} times. "
                    "Stop retrying it unchanged; inspect the error and change strategy."
                ),
                tool_name=tool_name,
                count=failure_count,
                signature=signature,
            )

        if self._is_idempotent(tool_name):
            previous = self._same_result_counts.get(signature)
            if previous is not None and previous[1] >= self.config.same_result_block_after:
                return ToolLoopGuardrailDecision(
                    action="block",
                    code="same_result_block",
                    message=(
                        f"Blocked {tool_name}: this read-only call returned the same result "
                        f"{previous[1]} times. Use the result already provided or change the query."
                    ),
                    tool_name=tool_name,
                    count=previous[1],
                    signature=signature,
                )

        return ToolLoopGuardrailDecision(tool_name=tool_name, signature=signature)

    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: str,
        *,
        failed: bool,
    ) -> ToolLoopGuardrailDecision:
        signature = ToolCallSignature.from_call(tool_name, _coerce_args(args))
        if failed:
            failure_count = self._failure_counts.get(signature, 0) + 1
            self._failure_counts[signature] = failure_count
            self._same_result_counts.pop(signature, None)
            if failure_count >= self.config.repeated_failure_warn_after:
                return ToolLoopGuardrailDecision(
                    action="warn",
                    code="repeated_failure_warning",
                    message=(
                        f"{tool_name} has failed {failure_count} times with identical arguments. "
                        "This looks like a loop; change strategy before retrying."
                    ),
                    tool_name=tool_name,
                    count=failure_count,
                    signature=signature,
                )
            return ToolLoopGuardrailDecision(tool_name=tool_name, count=failure_count, signature=signature)

        self._failure_counts.pop(signature, None)
        if not self._is_idempotent(tool_name):
            self._same_result_counts.pop(signature, None)
            return ToolLoopGuardrailDecision(tool_name=tool_name, signature=signature)

        result_hash = _result_hash(result)
        previous = self._same_result_counts.get(signature)
        repeat_count = 1
        if previous is not None and previous[0] == result_hash:
            repeat_count = previous[1] + 1
        self._same_result_counts[signature] = (result_hash, repeat_count)
        if repeat_count >= self.config.same_result_warn_after:
            return ToolLoopGuardrailDecision(
                action="warn",
                code="same_result_warning",
                message=(
                    f"{tool_name} returned the same result {repeat_count} times. "
                    "Use the result already provided or change the query instead of repeating it."
                ),
                tool_name=tool_name,
                count=repeat_count,
                signature=signature,
            )
        return ToolLoopGuardrailDecision(tool_name=tool_name, count=repeat_count, signature=signature)

    def _is_idempotent(self, tool_name: str) -> bool:
        if tool_name in self.config.mutating_tools:
            return False
        return tool_name in self.config.idempotent_tools


def build_toolguard_synthetic_result(decision: ToolLoopGuardrailDecision) -> str:
    """Build a synthetic tool result for a blocked call."""

    return json.dumps(
        {
            "ok": False,
            "error": decision.message,
            "error_type": "ToolGuardrailError",
            "category": "tool_guardrail",
            "guardrail": decision.to_metadata(),
        },
        ensure_ascii=False,
    )


def append_toolguard_guidance(result: str, decision: ToolLoopGuardrailDecision) -> str:
    """Append warning guidance to a tool result when useful."""

    if decision.action != "warn" or not decision.message:
        return result
    return f"{result}\n\n[Tool loop warning: {decision.code}; count={decision.count}; {decision.message}]"


def _coerce_args(args: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return args if isinstance(args, Mapping) else {}


def _canonical_args(args: Mapping[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _result_hash(result: str) -> str:
    text = str(result or "")
    try:
        parsed = json.loads(text)
    except Exception:
        return _sha256(text)
    try:
        canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        canonical = str(parsed)
    return _sha256(canonical)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
