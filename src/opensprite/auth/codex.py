"""OpenAI Codex OAuth token storage."""

from __future__ import annotations

import json
import time
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


CODEX_AUTH_RELATIVE_PATH = Path("auth") / "openai-codex.json"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_ISSUER = "https://auth.openai.com"
CODEX_OAUTH_TOKEN_URL = f"{CODEX_OAUTH_ISSUER}/oauth/token"
CODEX_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_REFRESH_SKEW_SECONDS = 120


class CodexAuthError(RuntimeError):
    """Raised when stored Codex OAuth credentials are missing or invalid."""


@dataclass(frozen=True)
class CodexAuthStatus:
    configured: bool
    path: Path
    expires_at: int | None = None
    expired: bool | None = None
    account_id: str | None = None


@dataclass(frozen=True)
class CodexToken:
    access_token: str
    refresh_token: str | None = None
    expires_at: int | None = None
    account_id: str | None = None
    scopes: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, path: Path) -> "CodexToken":
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise CodexAuthError(f"OpenAI Codex OAuth token file is missing access_token: {path}")
        raw_scopes = payload.get("scopes") or []
        scopes = tuple(str(item) for item in raw_scopes) if isinstance(raw_scopes, list) else ()
        expires_at = payload.get("expires_at")
        if expires_at is not None:
            try:
                expires_at = int(expires_at)
            except (TypeError, ValueError) as exc:
                raise CodexAuthError(f"OpenAI Codex OAuth expires_at must be an integer: {path}") from exc
        return cls(
            access_token=access_token,
            refresh_token=str(payload.get("refresh_token") or "").strip() or None,
            expires_at=expires_at,
            account_id=str(payload.get("account_id") or "").strip() or None,
            scopes=scopes,
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"access_token": self.access_token}
        if self.refresh_token:
            payload["refresh_token"] = self.refresh_token
        if self.expires_at is not None:
            payload["expires_at"] = self.expires_at
        if self.account_id:
            payload["account_id"] = self.account_id
        if self.scopes:
            payload["scopes"] = list(self.scopes)
        return payload


def default_app_home() -> Path:
    return Path.home() / ".opensprite"


def codex_auth_path(app_home: str | Path | None = None) -> Path:
    home = Path(app_home).expanduser() if app_home is not None else default_app_home()
    return home / CODEX_AUTH_RELATIVE_PATH


def load_codex_token(app_home: str | Path | None = None) -> CodexToken:
    path = codex_auth_path(app_home)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CodexAuthError(
            "OpenAI Codex OAuth is selected but no token is stored. "
            "Run `opensprite auth login openai-codex` when the login flow is available."
        ) from exc
    except json.JSONDecodeError as exc:
        raise CodexAuthError(f"OpenAI Codex OAuth token file is invalid: {path}") from exc
    if not isinstance(payload, dict):
        raise CodexAuthError(f"OpenAI Codex OAuth token file must contain a JSON object: {path}")
    return CodexToken.from_payload(payload, path=path)


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        data = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def infer_access_token_expiry(access_token: str) -> int | None:
    exp = _decode_jwt_payload(access_token).get("exp")
    try:
        return int(exp) if exp is not None else None
    except (TypeError, ValueError):
        return None


def access_token_is_expiring(access_token: str, skew_seconds: int = CODEX_REFRESH_SKEW_SECONDS) -> bool:
    expires_at = infer_access_token_expiry(access_token)
    if expires_at is None:
        return False
    return expires_at <= int(time.time()) + max(0, int(skew_seconds))


def refresh_codex_token(
    token: CodexToken,
    *,
    app_home: str | Path | None = None,
    timeout_seconds: float = 20.0,
) -> CodexToken:
    if not token.refresh_token:
        raise CodexAuthError("OpenAI Codex OAuth token is missing refresh_token; login again.")
    timeout = httpx.Timeout(max(5.0, float(timeout_seconds)))
    with httpx.Client(timeout=timeout, headers={"Accept": "application/json"}) as client:
        response = client.post(
            CODEX_OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": CODEX_OAUTH_CLIENT_ID,
            },
        )
    if response.status_code != 200:
        message = f"OpenAI Codex token refresh failed with status {response.status_code}."
        try:
            payload = response.json()
            err = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(err, dict) and err.get("message"):
                message = f"OpenAI Codex token refresh failed: {err['message']}"
            elif isinstance(err, str):
                description = payload.get("error_description") or payload.get("message") or err
                message = f"OpenAI Codex token refresh failed: {description}"
        except Exception:
            pass
        raise CodexAuthError(message)
    try:
        payload = response.json()
    except Exception as exc:
        raise CodexAuthError("OpenAI Codex token refresh returned invalid JSON.") from exc
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise CodexAuthError("OpenAI Codex token refresh response was missing access_token.")
    refreshed = CodexToken(
        access_token=access_token,
        refresh_token=str(payload.get("refresh_token") or "").strip() or token.refresh_token,
        expires_at=infer_access_token_expiry(access_token),
        account_id=token.account_id,
        scopes=token.scopes,
    )
    save_codex_token(refreshed, app_home)
    return refreshed


def load_or_refresh_codex_token(
    app_home: str | Path | None = None,
    *,
    force_refresh: bool = False,
    refresh_if_expiring: bool = True,
    refresh_skew_seconds: int = CODEX_REFRESH_SKEW_SECONDS,
) -> CodexToken:
    token = load_codex_token(app_home)
    should_refresh = force_refresh or (
        refresh_if_expiring and access_token_is_expiring(token.access_token, refresh_skew_seconds)
    )
    if should_refresh:
        return refresh_codex_token(token, app_home=app_home)
    return token


def codex_device_login(
    app_home: str | Path | None = None,
    *,
    timeout_seconds: float = 900.0,
    announce: Any | None = None,
) -> CodexToken:
    def emit(message: str) -> None:
        if announce is not None:
            announce(message)

    with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
        response = client.post(
            f"{CODEX_OAUTH_ISSUER}/api/accounts/deviceauth/usercode",
            json={"client_id": CODEX_OAUTH_CLIENT_ID},
            headers={"Content-Type": "application/json"},
        )
    if response.status_code != 200:
        raise CodexAuthError(f"OpenAI Codex device code request failed with status {response.status_code}.")
    device_data = response.json()
    user_code = str(device_data.get("user_code") or "").strip()
    device_auth_id = str(device_data.get("device_auth_id") or "").strip()
    poll_interval = max(3, int(device_data.get("interval") or 5))
    if not user_code or not device_auth_id:
        raise CodexAuthError("OpenAI Codex device code response missing required fields.")

    emit("To continue, open this URL in your browser:")
    emit(f"  {CODEX_OAUTH_ISSUER}/codex/device")
    emit("Enter this code:")
    emit(f"  {user_code}")

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    code_payload: dict[str, Any] | None = None
    with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            poll_response = client.post(
                f"{CODEX_OAUTH_ISSUER}/api/accounts/deviceauth/token",
                json={"device_auth_id": device_auth_id, "user_code": user_code},
                headers={"Content-Type": "application/json"},
            )
            if poll_response.status_code == 200:
                code_payload = poll_response.json()
                break
            if poll_response.status_code in (403, 404):
                continue
            raise CodexAuthError(f"OpenAI Codex device auth polling failed with status {poll_response.status_code}.")
    if code_payload is None:
        raise CodexAuthError("OpenAI Codex login timed out waiting for browser authorization.")

    authorization_code = str(code_payload.get("authorization_code") or "").strip()
    code_verifier = str(code_payload.get("code_verifier") or "").strip()
    if not authorization_code or not code_verifier:
        raise CodexAuthError("OpenAI Codex device auth response missing authorization_code or code_verifier.")

    with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
        token_response = client.post(
            CODEX_OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": f"{CODEX_OAUTH_ISSUER}/deviceauth/callback",
                "client_id": CODEX_OAUTH_CLIENT_ID,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if token_response.status_code != 200:
        raise CodexAuthError(f"OpenAI Codex token exchange failed with status {token_response.status_code}.")
    tokens = token_response.json()
    access_token = str(tokens.get("access_token") or "").strip()
    if not access_token:
        raise CodexAuthError("OpenAI Codex token exchange did not return access_token.")
    token = CodexToken(
        access_token=access_token,
        refresh_token=str(tokens.get("refresh_token") or "").strip() or None,
        expires_at=infer_access_token_expiry(access_token),
    )
    save_codex_token(token, app_home)
    return token


def save_codex_token(token: CodexToken, app_home: str | Path | None = None) -> Path:
    path = codex_auth_path(app_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token.to_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def delete_codex_token(app_home: str | Path | None = None) -> bool:
    path = codex_auth_path(app_home)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def get_codex_status(app_home: str | Path | None = None) -> CodexAuthStatus:
    path = codex_auth_path(app_home)
    if not path.exists():
        return CodexAuthStatus(configured=False, path=path)
    token = load_codex_token(app_home)
    expired = token.expires_at is not None and token.expires_at <= int(time.time())
    return CodexAuthStatus(
        configured=True,
        path=path,
        expires_at=token.expires_at,
        expired=expired,
        account_id=token.account_id,
    )
