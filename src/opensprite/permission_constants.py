"""Shared constants for tool permission configuration and policy."""

RISK_LEVEL_READ = "read"
RISK_LEVEL_WRITE = "write"
RISK_LEVEL_EXECUTE = "execute"
RISK_LEVEL_NETWORK = "network"
RISK_LEVEL_EXTERNAL_SIDE_EFFECT = "external_side_effect"
RISK_LEVEL_CONFIGURATION = "configuration"
RISK_LEVEL_DELEGATION = "delegation"
RISK_LEVEL_MEMORY = "memory"
RISK_LEVEL_MCP = "mcp"

ALL_RISK_LEVELS_ORDER = (
    RISK_LEVEL_READ,
    RISK_LEVEL_WRITE,
    RISK_LEVEL_EXECUTE,
    RISK_LEVEL_NETWORK,
    RISK_LEVEL_EXTERNAL_SIDE_EFFECT,
    RISK_LEVEL_CONFIGURATION,
    RISK_LEVEL_DELEGATION,
    RISK_LEVEL_MEMORY,
    RISK_LEVEL_MCP,
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
