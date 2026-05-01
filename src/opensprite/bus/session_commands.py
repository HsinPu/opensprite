"""Central registry for chat-session slash commands."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommandDef:
    """Definition for one chat-session slash command."""

    name: str
    description: str
    args_hint: str = ""
    aliases: tuple[str, ...] = ()
    handler_key: str | None = None
    category: str = "Session"
    subcommands: tuple[str, ...] = ()


COMMAND_REGISTRY: tuple[CommandDef, ...] = (
    CommandDef("help", "Show available chat commands or help for one command.", args_hint="[command]", category="Info"),
    CommandDef("stop", "Stop the current in-flight run for this session."),
    CommandDef("reset", "Reset this session's conversation state and derived history."),
    CommandDef(
        "cron",
        "Manage scheduled jobs for the current session.",
        args_hint="<subcommand>",
        category="Automation",
        subcommands=("add", "list", "pause", "enable", "run", "remove", "help"),
    ),
    CommandDef(
        "task",
        "Inspect or update the current session task state.",
        args_hint="<subcommand>",
        category="Work",
        subcommands=(
            "show",
            "history",
            "set",
            "activate",
            "reopen",
            "block",
            "wait",
            "step",
            "complete",
            "next",
            "done",
            "cancel",
            "reset",
            "help",
        ),
    ),
    CommandDef(
        "curator",
        "Inspect or manually control background curation for this session, including optional run scopes.",
        args_hint="<status|run [scope]|pause|resume|help>",
        category="Maintenance",
        subcommands=("status", "run", "pause", "resume", "help"),
    ),
)


def _build_command_lookup() -> dict[str, CommandDef]:
    lookup: dict[str, CommandDef] = {}
    for command in COMMAND_REGISTRY:
        lookup[command.name] = command
        for alias in command.aliases:
            lookup[alias] = command
    return lookup


_COMMAND_LOOKUP = _build_command_lookup()


def first_command_token(text: str | None) -> str:
    """Return the first token when it looks like a slash command, else empty."""
    raw = str(text or "").strip()
    if not raw.startswith("/"):
        return ""
    return raw.split(maxsplit=1)[0]


def normalize_command_name(token_or_name: str | None) -> str:
    """Normalize one command token or bare name to its lookup key."""
    normalized = str(token_or_name or "").strip().lower()
    if not normalized:
        return ""
    if normalized.startswith("/"):
        normalized = normalized[1:]
    if "@" in normalized:
        normalized = normalized.split("@", 1)[0]
    return normalized


def resolve_session_command(text_or_name: str | None) -> CommandDef | None:
    """Resolve a slash-command token or bare command name to a command definition."""
    raw = str(text_or_name or "").strip()
    if raw.startswith("/"):
        raw = raw.split(maxsplit=1)[0]
    else:
        raw = raw.split(maxsplit=1)[0] if raw else ""
    normalized = normalize_command_name(raw)
    if not normalized:
        return None
    return _COMMAND_LOOKUP.get(normalized)


def render_command_usage(command: CommandDef) -> str:
    """Return the canonical user-facing usage line for one command."""
    usage = f"/{command.name}"
    if command.args_hint:
        usage = f"{usage} {command.args_hint}"
    return usage


def iter_session_commands() -> tuple[CommandDef, ...]:
    """Return the stable ordered command registry."""
    return COMMAND_REGISTRY


def serialize_session_command(command: CommandDef) -> dict[str, object]:
    """Return JSON-safe metadata for one chat-session slash command."""
    return {
        "name": command.name,
        "command": f"/{command.name}",
        "usage": render_command_usage(command),
        "description": command.description,
        "args_hint": command.args_hint,
        "aliases": [f"/{alias}" for alias in command.aliases],
        "category": command.category,
        "subcommands": list(command.subcommands),
    }


def session_command_catalog() -> dict[str, object]:
    """Return the public command catalog derived from the registry."""
    commands = [serialize_session_command(command) for command in iter_session_commands()]
    categories: list[dict[str, object]] = []
    by_category: dict[str, list[str]] = {}
    for command in iter_session_commands():
        by_category.setdefault(command.category, []).append(command.name)
    for category, names in by_category.items():
        categories.append({"name": category, "commands": names})
    return {"commands": commands, "categories": categories}
