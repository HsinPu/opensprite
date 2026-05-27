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

APPROVAL_MODE_AUTO = "auto"
APPROVAL_MODE_ASK = "ask"
APPROVAL_MODE_BLOCK = "block"

DEFAULT_APPROVAL_MODE = APPROVAL_MODE_AUTO

APPROVAL_MODES_ORDER = (APPROVAL_MODE_AUTO, APPROVAL_MODE_ASK, APPROVAL_MODE_BLOCK)

APPROVAL_MODES = frozenset(APPROVAL_MODES_ORDER)

PERMISSION_PROFILE_NAMES = frozenset({"chat", "research", "coding", "media", "ops"})


def denied_risks_except(allowed: tuple[str, ...] | list[str] | set[str] | frozenset[str]) -> tuple[str, ...]:
    """Return all known risk levels not present in the allowed set."""
    allowed_set = set(allowed)
    return tuple(risk for risk in ALL_RISK_LEVELS_ORDER if risk not in allowed_set)
