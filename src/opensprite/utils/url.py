"""URL helpers for API clients."""

from __future__ import annotations


def join_url_path(base_url: str, endpoint_path: str) -> str:
    """Join an API base URL and endpoint path without duplicating overlapping path segments."""
    base = str(base_url or "").strip().rstrip("/")
    endpoint = "/" + str(endpoint_path or "").strip().lstrip("/")
    if not base:
        return endpoint

    endpoint_parts = [part for part in endpoint.strip("/").split("/") if part]
    lowered_base = base.lower()
    for overlap in range(len(endpoint_parts), 0, -1):
        prefix = "/" + "/".join(endpoint_parts[:overlap])
        if lowered_base.endswith(prefix.lower()):
            remainder = "/".join(endpoint_parts[overlap:])
            return base if not remainder else f"{base}/{remainder}"
    return f"{base}{endpoint}"
