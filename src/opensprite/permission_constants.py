"""Shared constants for tool permission configuration and policy."""

ALL_RISK_LEVELS_ORDER = (
    "read",
    "write",
    "execute",
    "network",
    "external_side_effect",
    "configuration",
    "delegation",
    "memory",
    "mcp",
)

ALL_RISK_LEVELS = frozenset(ALL_RISK_LEVELS_ORDER)

APPROVAL_MODES = frozenset({"auto", "ask", "block"})
