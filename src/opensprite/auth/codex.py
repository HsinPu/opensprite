"""OpenAI Codex OAuth token storage."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CODEX_AUTH_RELATIVE_PATH = Path("auth") / "openai-codex.json"


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
