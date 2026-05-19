"""Tool registry for managing available tools."""

from typing import Any, Awaitable, Callable

from .base import Tool
from .evidence import ToolEvidence, build_tool_evidence
from .permissions import PermissionApprovalResult, PermissionDecision, ToolPermissionPolicy


PermissionRequestHandler = Callable[[str, Any, PermissionDecision], Awaitable[PermissionApprovalResult]]
PermissionDecisionHook = Callable[[str, str, dict[str, Any]], Awaitable[None]]
BeforeToolExecuteHook = Callable[[str, dict[str, Any]], Awaitable[None]]


class ToolRegistry:
    """Registry for managing agent tools."""

    def __init__(self, permission_policy: ToolPermissionPolicy | None = None):
        self._tools: dict[str, Tool] = {}
        self._permission_policy = permission_policy or ToolPermissionPolicy.allow_all()
        self._permission_request_handler: PermissionRequestHandler | None = None
        self._permission_decision_hook: PermissionDecisionHook | None = None
        self.permission_resolution_metadata: dict[str, Any] | None = None

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def set_permission_policy(self, permission_policy: ToolPermissionPolicy) -> None:
        """Set the permission policy used for tool exposure and execution."""
        self._permission_policy = permission_policy

    def set_permission_request_handler(self, handler: PermissionRequestHandler | None) -> None:
        """Set the async approval hook used when ask-mode permissions need a decision."""
        self._permission_request_handler = handler

    def set_permission_decision_hook(self, hook: PermissionDecisionHook | None) -> None:
        """Set the async trace hook used for permission decisions."""
        self._permission_decision_hook = hook

    @property
    def permission_policy(self) -> ToolPermissionPolicy:
        """Return the active permission policy."""
        return self._permission_policy

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def unregister(self, name: str) -> Tool | None:
        """Remove one registered tool by name."""
        return self._tools.pop(name, None)

    def filtered(
        self,
        *,
        include_names: set[str] | frozenset[str] | None = None,
        exclude_names: set[str] | frozenset[str] | None = None,
        permission_policy: ToolPermissionPolicy | None = None,
    ) -> "ToolRegistry":
        """Return a registry copy filtered by included/excluded tool names."""
        filtered_registry = ToolRegistry(permission_policy=permission_policy or self._permission_policy)
        filtered_registry.set_permission_request_handler(self._permission_request_handler)
        filtered_registry.set_permission_decision_hook(self._permission_decision_hook)
        filtered_registry.permission_resolution_metadata = self.permission_resolution_metadata
        included = include_names
        excluded = exclude_names or set()
        for name, tool in self._tools.items():
            if included is not None and name not in included:
                continue
            if name in excluded:
                continue
            filtered_registry.register(tool)
        return filtered_registry

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        return [
            tool.to_schema()
            for tool in self._tools.values()
            if self._permission_policy.is_tool_exposed(tool.name, tool_risk_levels=tool.risk_levels)
        ]

    async def execute(
        self,
        name: str,
        params: Any,
        *,
        on_before_execute: BeforeToolExecuteHook | None = None,
    ) -> str:
        """Execute a tool by name with given parameters."""
        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"
        display_params = tool.sanitize_params_for_display(params)

        decision = self._permission_policy.check(name, display_params, tool_risk_levels=tool.risk_levels)
        await self._emit_permission_decision("tool_permission.checked", name, decision, display_params)
        if not decision.allowed:
            if decision.requires_approval and self._permission_request_handler is not None:
                await self._emit_permission_decision("tool_permission.approval_required", name, decision, display_params)
                approval = await self._permission_request_handler(name, display_params, decision)
                if approval.approved:
                    if on_before_execute is not None:
                        await on_before_execute(name, display_params if isinstance(display_params, dict) else {})
                    return await tool.execute_validated(params)
                reason = approval.reason or decision.reason or "user denied approval"
                return f"Error: Tool '{name}' blocked by permission policy: {reason}."
            await self._emit_permission_decision("tool_permission.denied", name, decision, display_params)
            return f"Error: Tool '{name}' blocked by permission policy: {decision.reason}."

        await self._emit_permission_decision("tool_permission.allowed", name, decision, display_params)
        if on_before_execute is not None:
            await on_before_execute(name, display_params if isinstance(display_params, dict) else {})
        return await tool.execute_validated(params)

    async def _emit_permission_decision(
        self,
        event_type: str,
        tool_name: str,
        decision: PermissionDecision,
        params: Any,
    ) -> None:
        if self._permission_decision_hook is None:
            return
        payload = {
            "tool_name": tool_name,
            "allowed": decision.allowed,
            "decision": _decision_label(event_type, decision),
            "reason": decision.reason,
            "requires_approval": decision.requires_approval,
            "risk_levels": list(decision.risk_levels),
            "approval_mode": decision.approval_mode,
            "matched_allowed_tools": list(decision.matched_allowed_tools),
            "matched_denied_tools": list(decision.matched_denied_tools),
            "matched_allowed_risk_levels": list(decision.matched_allowed_risk_levels),
            "matched_denied_risk_levels": list(decision.matched_denied_risk_levels),
            "matched_approval_required_tools": list(decision.matched_approval_required_tools),
            "matched_approval_required_risk_levels": list(decision.matched_approval_required_risk_levels),
            "params": params if isinstance(params, dict) else {},
        }
        await self._permission_decision_hook(event_type, tool_name, payload)

    def build_evidence(self, name: str, params: Any, result: str, *, ok: bool) -> ToolEvidence:
        """Build tool-specific completion evidence when the tool supports it."""
        tool = self._tools.get(name)
        safe_params = params if isinstance(params, dict) else {}
        if tool is None:
            return build_tool_evidence(name, safe_params, result, ok=ok)
        return tool.build_evidence(safe_params, result, ok=ok)

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return [
            name
            for name, tool in self._tools.items()
            if self._permission_policy.is_tool_exposed(name, tool_risk_levels=tool.risk_levels)
        ]


def _decision_label(event_type: str, decision: PermissionDecision) -> str:
    if event_type.endswith(".approval_required") or decision.requires_approval:
        return "approval_required"
    if event_type.endswith(".denied") or not decision.allowed:
        return "denied"
    if event_type.endswith(".allowed"):
        return "allowed"
    return "checked"
