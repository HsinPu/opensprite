"""Shared policy for workspace-grounded final answers."""

from __future__ import annotations

import re


WORKSPACE_LOCATION_SYMBOL_RE = re.compile(r"\b(?:function|class|method|symbol)\s+[`'\"]?[\w.:-]+")
WORKSPACE_LOCATION_QUOTED_TOKEN_RE = re.compile(r"[`'\"][\w.:-]+[`'\"]")
WORKSPACE_PATH_RE = re.compile(
    r"(?:[\w.-]+[\\/])+[\w.-]+|[\w.-]+\.(?:py|js|ts|tsx|jsx|vue|json|toml|yaml|yml|md|css|html|java|go|rs|sql)",
    flags=re.IGNORECASE,
)


def contains_workspace_location_clue(response_text: str | None, *, has_workspace_path: bool = False) -> bool:
    """Return whether a final answer identifies a concrete workspace location."""
    if has_workspace_path:
        return True
    normalized = str(response_text or "").strip().lower()
    if not normalized:
        return False
    if WORKSPACE_LOCATION_SYMBOL_RE.search(normalized):
        return True
    return bool(WORKSPACE_LOCATION_QUOTED_TOKEN_RE.search(normalized))


def workspace_paths(text: str | None) -> tuple[str, ...]:
    matches = WORKSPACE_PATH_RE.findall(str(text or ""))
    seen: set[str] = set()
    paths: list[str] = []
    for match in matches:
        normalized = match.strip().lower().replace("\\", "/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)
    return tuple(paths)


def response_references_workspace_path(path: str, normalized_response: str) -> bool:
    normalized_path = str(path or "").lower().replace("\\", "/")
    if normalized_path in str(normalized_response or "").replace("\\", "/"):
        return True
    filename = normalized_path.rsplit("/", 1)[-1]
    return bool(filename and filename in normalized_response)
