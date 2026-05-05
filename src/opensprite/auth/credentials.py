"""Application-scoped credential vault for API keys and tokens."""

from __future__ import annotations

import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUTH_STORE_VERSION = 1
AUTH_STORE_RELATIVE_PATH = Path("auth.json")
DEFAULT_LLM_CAPABILITY = "llm.chat"


class CredentialStoreError(RuntimeError):
    """Raised when credential storage or resolution fails."""


class CredentialNotFoundError(CredentialStoreError):
    """Raised when a requested credential cannot be found."""


@dataclass(frozen=True)
class ResolvedCredential:
    provider: str
    id: str
    label: str
    auth_type: str
    secret: str
    base_url: str | None = None
    scopes: tuple[str, ...] = ()


def default_app_home() -> Path:
    return Path.home() / ".opensprite"


def auth_store_path(app_home: str | Path | None = None) -> Path:
    home = Path(app_home).expanduser() if app_home is not None else default_app_home()
    return home / AUTH_STORE_RELATIVE_PATH


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_store() -> dict[str, Any]:
    return {
        "version": AUTH_STORE_VERSION,
        "credentials": {},
        "defaults": {"providers": {}, "capabilities": {}},
    }


def _normalize_store(raw: dict[str, Any]) -> dict[str, Any]:
    store = dict(raw)
    store["version"] = AUTH_STORE_VERSION
    if not isinstance(store.get("credentials"), dict):
        store["credentials"] = {}
    defaults = store.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
        store["defaults"] = defaults
    if not isinstance(defaults.get("providers"), dict):
        defaults["providers"] = {}
    if not isinstance(defaults.get("capabilities"), dict):
        defaults["capabilities"] = {}
    return store


def load_auth_store(app_home: str | Path | None = None) -> dict[str, Any]:
    """Load the OpenSprite credential store, preserving corrupt files."""
    path = auth_store_path(app_home)
    if not path.exists():
        return _empty_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        corrupt_path = path.with_suffix(".json.corrupt")
        try:
            shutil.copy2(path, corrupt_path)
        except OSError:
            pass
        raise CredentialStoreError(f"Credential store is invalid JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise CredentialStoreError(f"Credential store must contain a JSON object: {path}")
    return _normalize_store(raw)


def save_auth_store(store: dict[str, Any], app_home: str | Path | None = None) -> Path:
    """Persist the credential store with an atomic replace."""
    path = auth_store_path(app_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _normalize_store(store)
    payload["updated_at"] = _now_iso()
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return path


def redact_secret(secret: str, *, visible: int = 4) -> str:
    value = str(secret or "")
    if not value:
        return ""
    if len(value) <= visible * 2:
        return "*" * len(value)
    return f"{value[:visible]}...{value[-visible:]}"


def _provider_entries(store: dict[str, Any], provider: str) -> list[dict[str, Any]]:
    credentials = store.setdefault("credentials", {})
    entries = credentials.get(provider)
    if not isinstance(entries, list):
        entries = []
        credentials[provider] = entries
    return entries


def _public_entry(provider: str, entry: dict[str, Any], *, default_id: str | None = None) -> dict[str, Any]:
    secret = str(entry.get("secret") or "")
    return {
        "id": str(entry.get("id") or ""),
        "provider": provider,
        "label": str(entry.get("label") or ""),
        "auth_type": str(entry.get("auth_type") or "api_key"),
        "base_url": entry.get("base_url"),
        "scopes": list(entry.get("scopes") or []),
        "priority": int(entry.get("priority") or 0),
        "status": str(entry.get("status") or "ok"),
        "secret_configured": bool(secret),
        "secret_preview": redact_secret(secret),
        "request_count": int(entry.get("request_count") or 0),
        "last_used_at": entry.get("last_used_at"),
        "created_at": entry.get("created_at"),
        "is_default": bool(default_id and entry.get("id") == default_id),
    }


def list_credentials(provider: str | None = None, *, app_home: str | Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Return redacted credential entries grouped by provider."""
    store = load_auth_store(app_home)
    credentials = store.get("credentials", {})
    defaults = store.get("defaults", {}).get("providers", {})
    providers = [provider] if provider else sorted(credentials)
    result: dict[str, list[dict[str, Any]]] = {}
    for provider_name in providers:
        raw_entries = credentials.get(provider_name, [])
        if not isinstance(raw_entries, list):
            raw_entries = []
        default_id = defaults.get(provider_name) if isinstance(defaults, dict) else None
        result[provider_name] = [_public_entry(provider_name, entry, default_id=default_id) for entry in raw_entries if isinstance(entry, dict)]
    return result


def find_existing_credential(
    provider: str,
    secret: str,
    *,
    app_home: str | Path | None = None,
) -> dict[str, Any] | None:
    store = load_auth_store(app_home)
    for entry in _provider_entries(store, provider):
        if isinstance(entry, dict) and str(entry.get("secret") or "") == secret:
            return dict(entry)
    return None


def add_credential(
    provider: str,
    secret: str,
    *,
    label: str | None = None,
    auth_type: str = "api_key",
    base_url: str | None = None,
    scopes: list[str] | tuple[str, ...] | None = None,
    set_default: bool = True,
    app_home: str | Path | None = None,
) -> dict[str, Any]:
    """Add or update one credential and return its redacted public entry."""
    provider = str(provider or "").strip()
    secret = str(secret or "").strip()
    if not provider:
        raise CredentialStoreError("provider is required")
    if not secret:
        raise CredentialStoreError("secret is required")

    store = load_auth_store(app_home)
    entries = _provider_entries(store, provider)
    normalized_scopes = [str(scope).strip() for scope in (scopes or [DEFAULT_LLM_CAPABILITY]) if str(scope).strip()]
    existing = next((entry for entry in entries if isinstance(entry, dict) and str(entry.get("secret") or "") == secret), None)
    if existing is None:
        entry = {
            "id": f"cred_{uuid.uuid4().hex[:12]}",
            "label": str(label or "").strip() or f"{provider} key {len(entries) + 1}",
            "auth_type": auth_type,
            "secret": secret,
            "base_url": str(base_url or "").strip() or None,
            "scopes": normalized_scopes,
            "priority": len(entries),
            "status": "ok",
            "request_count": 0,
            "last_used_at": None,
            "created_at": _now_iso(),
        }
        entries.append(entry)
    else:
        entry = existing
        if label:
            entry["label"] = str(label).strip()
        if base_url:
            entry["base_url"] = str(base_url).strip()
        entry["auth_type"] = auth_type
        entry["scopes"] = normalized_scopes
        entry["status"] = "ok"

    defaults = store.setdefault("defaults", {})
    provider_defaults = defaults.setdefault("providers", {})
    capability_defaults = defaults.setdefault("capabilities", {})
    if set_default or not provider_defaults.get(provider):
        provider_defaults[provider] = entry["id"]
    for scope in normalized_scopes:
        capability_defaults.setdefault(scope, entry["id"])

    save_auth_store(store, app_home)
    return _public_entry(provider, entry, default_id=provider_defaults.get(provider))


def _find_entry(store: dict[str, Any], provider: str | None, credential_id: str) -> tuple[str, dict[str, Any]] | None:
    credentials = store.get("credentials", {})
    provider_names = [provider] if provider else sorted(credentials)
    for provider_name in provider_names:
        entries = credentials.get(provider_name, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id") == credential_id:
                return provider_name, entry
    return None


def resolve_credential(
    *,
    provider: str | None = None,
    credential_id: str | None = None,
    capability: str | None = None,
    app_home: str | Path | None = None,
) -> ResolvedCredential:
    """Resolve a credential by id, provider default, or capability default."""
    store = load_auth_store(app_home)
    target_id = str(credential_id or "").strip()
    defaults = store.get("defaults", {})
    if not target_id and provider:
        target_id = str(defaults.get("providers", {}).get(provider) or "").strip()
    if not target_id and capability:
        target_id = str(defaults.get("capabilities", {}).get(capability) or "").strip()
    if target_id:
        found = _find_entry(store, provider, target_id)
        if found is None and provider:
            found = _find_entry(store, None, target_id)
        if found is None:
            raise CredentialNotFoundError(f"Credential not found: {target_id}")
        resolved_provider, entry = found
    else:
        entries = [entry for entry in _provider_entries(store, provider or "") if isinstance(entry, dict)] if provider else []
        candidates = [entry for entry in entries if str(entry.get("secret") or "").strip()]
        if not candidates:
            raise CredentialNotFoundError(f"No credential configured for provider: {provider or capability or 'unknown'}")
        entry = sorted(candidates, key=lambda item: int(item.get("priority") or 0))[0]
        resolved_provider = provider or ""

    secret = str(entry.get("secret") or "").strip()
    if not secret:
        raise CredentialNotFoundError(f"Credential is missing a secret: {entry.get('id')}")
    scopes = tuple(str(scope) for scope in (entry.get("scopes") or []) if str(scope).strip())
    return ResolvedCredential(
        provider=resolved_provider,
        id=str(entry.get("id") or ""),
        label=str(entry.get("label") or ""),
        auth_type=str(entry.get("auth_type") or "api_key"),
        secret=secret,
        base_url=str(entry.get("base_url") or "").strip() or None,
        scopes=scopes,
    )


def mark_credential_used(
    provider: str,
    credential_id: str,
    *,
    app_home: str | Path | None = None,
) -> None:
    store = load_auth_store(app_home)
    found = _find_entry(store, provider, credential_id)
    if found is None:
        return
    _, entry = found
    entry["last_used_at"] = _now_iso()
    entry["request_count"] = int(entry.get("request_count") or 0) + 1
    save_auth_store(store, app_home)


def set_provider_default(provider: str, credential_id: str, *, app_home: str | Path | None = None) -> dict[str, Any]:
    store = load_auth_store(app_home)
    found = _find_entry(store, provider, credential_id)
    if found is None:
        raise CredentialNotFoundError(f"Credential not found: {credential_id}")
    store.setdefault("defaults", {}).setdefault("providers", {})[provider] = credential_id
    save_auth_store(store, app_home)
    return _public_entry(provider, found[1], default_id=credential_id)


def set_capability_default(capability: str, credential_id: str, *, app_home: str | Path | None = None) -> dict[str, Any]:
    store = load_auth_store(app_home)
    found = _find_entry(store, None, credential_id)
    if found is None:
        raise CredentialNotFoundError(f"Credential not found: {credential_id}")
    store.setdefault("defaults", {}).setdefault("capabilities", {})[capability] = credential_id
    save_auth_store(store, app_home)
    return _public_entry(found[0], found[1], default_id=credential_id)


def remove_credential(provider: str, credential_id: str, *, app_home: str | Path | None = None) -> dict[str, Any]:
    store = load_auth_store(app_home)
    entries = _provider_entries(store, provider)
    next_entries = [entry for entry in entries if not (isinstance(entry, dict) and entry.get("id") == credential_id)]
    if len(next_entries) == len(entries):
        raise CredentialNotFoundError(f"Credential not found: {credential_id}")
    store["credentials"][provider] = next_entries
    defaults = store.setdefault("defaults", {})
    provider_defaults = defaults.setdefault("providers", {})
    if provider_defaults.get(provider) == credential_id:
        if next_entries:
            provider_defaults[provider] = next_entries[0].get("id")
        else:
            provider_defaults.pop(provider, None)
    capability_defaults = defaults.setdefault("capabilities", {})
    for capability, default_id in list(capability_defaults.items()):
        if default_id == credential_id:
            capability_defaults.pop(capability, None)
    save_auth_store(store, app_home)
    return {"ok": True, "provider": provider, "credential_id": credential_id}
