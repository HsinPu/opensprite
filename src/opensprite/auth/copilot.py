"""GitHub Copilot token helpers."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any


COPILOT_BASE_URL = "https://api.githubcopilot.com"
COPILOT_MODELS_URL = f"{COPILOT_BASE_URL}/models"
COPILOT_TOKEN_EXCHANGE_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_EDITOR_VERSION = "vscode/1.104.1"
COPILOT_EXCHANGE_USER_AGENT = "GitHubCopilotChat/0.26.7"
COPILOT_REQUEST_USER_AGENT = "OpenSprite/0.1"
COPILOT_TOKEN_REFRESH_MARGIN_SECONDS = 120
COPILOT_AUTH_RELATIVE_PATH = Path("auth") / "github-copilot.json"
COPILOT_OAUTH_CLIENT_ID = "Ov23li8tweQw6odWQebz"

_CLASSIC_PAT_PREFIX = "ghp_"
_SUPPORTED_PREFIXES = ("gho_", "github_pat_", "ghu_")
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class CopilotAuthError(RuntimeError):
    """Raised when a GitHub token cannot be used for Copilot."""


@dataclass(frozen=True)
class CopilotToken:
    access_token: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, path: Path) -> "CopilotToken":
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise CopilotAuthError(f"GitHub Copilot token file is missing access_token: {path}")
        validate_copilot_token(access_token)
        return cls(access_token=access_token)

    def to_payload(self) -> dict[str, Any]:
        return {"access_token": self.access_token}


@dataclass(frozen=True)
class CopilotAuthStatus:
    configured: bool
    path: Path


@dataclass(frozen=True)
class CopilotDeviceAuth:
    user_code: str
    device_code: str
    verification_uri: str
    poll_interval: int
    expires_in: int | None = None


@dataclass(frozen=True)
class CopilotDevicePollResult:
    status: str
    token: CopilotToken | None = None


def default_app_home() -> Path:
    return Path.home() / ".opensprite"


def copilot_auth_path(app_home: str | Path | None = None) -> Path:
    home = Path(app_home).expanduser() if app_home is not None else default_app_home()
    return home / COPILOT_AUTH_RELATIVE_PATH


def load_copilot_token(app_home: str | Path | None = None) -> CopilotToken:
    path = copilot_auth_path(app_home)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CopilotAuthError("GitHub Copilot OAuth is selected but no token is stored.") from exc
    except json.JSONDecodeError as exc:
        raise CopilotAuthError(f"GitHub Copilot token file is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise CopilotAuthError(f"GitHub Copilot token file must contain a JSON object: {path}")
    return CopilotToken.from_payload(payload, path=path)


def save_copilot_token(token: CopilotToken, app_home: str | Path | None = None) -> Path:
    path = copilot_auth_path(app_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token.to_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def delete_copilot_token(app_home: str | Path | None = None) -> bool:
    try:
        copilot_auth_path(app_home).unlink()
        return True
    except FileNotFoundError:
        return False


def get_copilot_status(app_home: str | Path | None = None) -> CopilotAuthStatus:
    path = copilot_auth_path(app_home)
    if not path.exists():
        return CopilotAuthStatus(configured=False, path=path)
    load_copilot_token(app_home)
    return CopilotAuthStatus(configured=True, path=path)


def validate_copilot_token(token: str) -> None:
    normalized = str(token or "").strip()
    if not normalized:
        raise CopilotAuthError("GitHub Copilot token is required.")
    if normalized.startswith(_CLASSIC_PAT_PREFIX):
        raise CopilotAuthError(
            "Classic GitHub PATs (ghp_*) are not supported by the Copilot API. "
            "Use a GitHub OAuth token, GitHub App token, or fine-grained PAT with Copilot access."
        )


def copilot_start_device_auth(*, timeout_seconds: float = 15.0) -> CopilotDeviceAuth:
    data = urllib.parse.urlencode({"client_id": COPILOT_OAUTH_CLIENT_ID, "scope": "read:user"}).encode()
    request = urllib.request.Request(
        "https://github.com/login/device/code",
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(5.0, float(timeout_seconds))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise CopilotAuthError(f"GitHub Copilot device code request failed: {exc}") from exc
    user_code = str(payload.get("user_code") or "").strip()
    device_code = str(payload.get("device_code") or "").strip()
    verification_uri = str(payload.get("verification_uri") or "https://github.com/login/device").strip()
    if not user_code or not device_code:
        raise CopilotAuthError("GitHub Copilot device code response missing required fields.")
    try:
        interval = int(payload.get("interval") or 5)
    except (TypeError, ValueError):
        interval = 5
    try:
        expires_in = int(payload.get("expires_in")) if payload.get("expires_in") is not None else None
    except (TypeError, ValueError):
        expires_in = None
    return CopilotDeviceAuth(user_code, device_code, verification_uri, max(1, interval), expires_in)


def copilot_poll_device_auth(
    device_code: str,
    *,
    app_home: str | Path | None = None,
    timeout_seconds: float = 15.0,
) -> CopilotDevicePollResult:
    normalized_device_code = str(device_code or "").strip()
    if not normalized_device_code:
        raise CopilotAuthError("GitHub Copilot device auth polling requires device_code.")
    data = urllib.parse.urlencode(
        {
            "client_id": COPILOT_OAUTH_CLIENT_ID,
            "device_code": normalized_device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
    ).encode()
    request = urllib.request.Request(
        "https://github.com/login/oauth/access_token",
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(5.0, float(timeout_seconds))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise CopilotAuthError(f"GitHub Copilot device auth polling failed: {exc}") from exc
    access_token = str(payload.get("access_token") or "").strip()
    if access_token:
        token = CopilotToken(access_token=access_token)
        validate_copilot_token(token.access_token)
        save_copilot_token(token, app_home)
        return CopilotDevicePollResult(status="authorized", token=token)
    error = str(payload.get("error") or "").strip()
    if error in {"authorization_pending", "slow_down"}:
        return CopilotDevicePollResult(status="pending")
    if error in {"expired_token", "access_denied"}:
        return CopilotDevicePollResult(status=error)
    if error:
        raise CopilotAuthError(f"GitHub Copilot device auth failed: {error}")
    return CopilotDevicePollResult(status="pending")


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def exchange_copilot_token(raw_token: str, *, timeout_seconds: float = 10.0) -> tuple[str, float]:
    """Exchange a GitHub token for a short-lived Copilot API token."""
    validate_copilot_token(raw_token)
    normalized = raw_token.strip()
    fingerprint = _token_fingerprint(normalized)
    cached = _TOKEN_CACHE.get(fingerprint)
    if cached:
        api_token, expires_at = cached
        if time.time() < expires_at - COPILOT_TOKEN_REFRESH_MARGIN_SECONDS:
            return api_token, expires_at

    request = urllib.request.Request(
        COPILOT_TOKEN_EXCHANGE_URL,
        method="GET",
        headers={
            "Authorization": f"token {normalized}",
            "User-Agent": COPILOT_EXCHANGE_USER_AGENT,
            "Accept": "application/json",
            "Editor-Version": COPILOT_EDITOR_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(5.0, float(timeout_seconds))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise CopilotAuthError(f"GitHub Copilot token exchange failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise CopilotAuthError("GitHub Copilot token exchange returned invalid JSON.")
    api_token = str(payload.get("token") or "").strip()
    if not api_token:
        raise CopilotAuthError("GitHub Copilot token exchange returned no token.")
    try:
        expires_at = float(payload.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at <= 0:
        expires_at = time.time() + 1800
    _TOKEN_CACHE[fingerprint] = (api_token, expires_at)
    return api_token, expires_at


def copilot_request_headers(*, is_vision: bool = False) -> dict[str, str]:
    headers = {
        "Editor-Version": COPILOT_EDITOR_VERSION,
        "User-Agent": COPILOT_REQUEST_USER_AGENT,
        "Copilot-Integration-Id": "vscode-chat",
        "Openai-Intent": "conversation-edits",
        "x-initiator": "agent",
    }
    if is_vision:
        headers["Copilot-Vision-Request"] = "true"
    return headers


def _copilot_catalog_item_is_text_model(item: dict[str, Any]) -> bool:
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return False
    if item.get("model_picker_enabled") is False:
        return False
    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict):
        model_type = str(capabilities.get("type") or "").strip().lower()
        if model_type and model_type != "chat":
            return False
    endpoints = item.get("supported_endpoints")
    if isinstance(endpoints, list):
        normalized = {str(endpoint).strip() for endpoint in endpoints if str(endpoint).strip()}
        if normalized and not normalized.intersection({"/chat/completions", "/responses", "/v1/messages"}):
            return False
    return True


def fetch_copilot_models(api_key: str, *, timeout_seconds: float = 8.0) -> list[str]:
    """Fetch the live GitHub Copilot model catalog for this account."""
    token, _expires_at = exchange_copilot_token(api_key, timeout_seconds=timeout_seconds)
    request = urllib.request.Request(
        COPILOT_MODELS_URL,
        headers={**copilot_request_headers(), "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(5.0, float(timeout_seconds))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    seen: set[str] = set()
    models: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not _copilot_catalog_item_is_text_model(item):
            continue
        model_id = str(item.get("id") or "").strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            models.append(model_id)
    return models
