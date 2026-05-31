"""Provider-aware LLM context window resolution."""

from __future__ import annotations

from urllib.parse import urlparse


MINIMAX_CONTEXT_WINDOW_TOKENS = 204_800

_MINIMAX_PROVIDER_IDS = frozenset({"minimax"})
_MINIMAX_HOST_SUFFIXES = ("api.minimax.io", "api.minimaxi.com")


def resolve_context_window_tokens(
    *,
    provider_name: str | None,
    model: str | None,
    base_url: str | None = None,
    configured_context_window_tokens: int | None = None,
) -> int | None:
    """Return the effective context window for a provider/model route.

    Resolution intentionally starts with persisted config/metadata. Provider
    defaults are only fallback values for routes that do not expose model
    metadata themselves, such as direct MiniMax Anthropic-compatible endpoints.
    """
    if configured_context_window_tokens is not None and configured_context_window_tokens > 0:
        return configured_context_window_tokens

    provider_id = str(provider_name or "").strip().lower()
    model_id = str(model or "").strip()
    if provider_id in _MINIMAX_PROVIDER_IDS and _is_minimax_model(model_id):
        return MINIMAX_CONTEXT_WINDOW_TOKENS

    if _is_minimax_base_url(base_url) and _is_minimax_model(model_id):
        return MINIMAX_CONTEXT_WINDOW_TOKENS

    return None


def _is_minimax_model(model: str) -> bool:
    normalized = model.strip().lower()
    return normalized.startswith("minimax-m2") or normalized.startswith("minimaxai/minimax-m2")


def _is_minimax_base_url(base_url: str | None) -> bool:
    raw = str(base_url or "").strip()
    if not raw:
        return False
    try:
        host = urlparse(raw).hostname or ""
    except Exception:
        host = raw
    host = host.strip().lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in _MINIMAX_HOST_SUFFIXES)
