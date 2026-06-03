"""Shared policy for command-version answer grounding."""

from __future__ import annotations

import re


REPOSITORY_STATE_GIT_SUBCOMMANDS = frozenset({"rev-parse", "status", "log", "show", "branch"})


def command_inspects_git_repository_state(command: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", str(command or "").strip().lower())
    if not normalized.startswith("git "):
        return False
    return any(f"git {subcommand}" in normalized for subcommand in REPOSITORY_STATE_GIT_SUBCOMMANDS)
