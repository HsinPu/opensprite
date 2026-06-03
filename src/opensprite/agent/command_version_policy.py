"""Shared policy for command-version answer grounding."""

from __future__ import annotations

import re


REPOSITORY_STATE_GIT_SUBCOMMANDS = frozenset({"rev-parse", "status", "log", "show", "branch"})


def command_inspects_git_repository_state(command: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", str(command or "").strip().lower())
    if not normalized.startswith("git "):
        return False
    return any(f"git {subcommand}" in normalized for subcommand in REPOSITORY_STATE_GIT_SUBCOMMANDS)


def command_version_follow_up_instruction() -> str:
    return (
        "\n- Quality follow-up: the user asked for the installed command/program version. "
        "Run the direct version command, such as `<command> --version`, and answer with the version value. "
        "Do not inspect `.git`, `HEAD`, repository commits, or package metadata unless the user asks for repository state."
    )
