"""Runtime helpers for local browser automation tools."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


SUPPORTED_BROWSER_BACKENDS = ("agent-browser", "browserbase", "browser-use", "firecrawl")
CLOUD_BROWSER_BACKENDS = ("browserbase", "browser-use", "firecrawl")


class BrowserRuntimeError(RuntimeError):
    """Raised when browser automation cannot run."""


@dataclass
class CloudBrowserSession:
    provider_session_id: str
    cdp_url: str
    expires_at: float


class CloudBrowserProvider:
    """Creates browser CDP sessions for cloud browser backends."""

    backend = ""
    display_name = "Cloud browser"

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    def is_configured(self) -> bool:
        return False

    def status(self) -> dict[str, Any]:
        return {"configured": self.is_configured()}

    async def create_session(self, *, session_key: str, session_timeout: int, timeout: int) -> CloudBrowserSession:
        raise NotImplementedError

    async def close_session(self, provider_session_id: str, *, timeout: int) -> bool:
        return False

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        timeout: int = 30,
        error_prefix: str,
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(
                timeout=max(1, int(timeout or 30)),
                follow_redirects=True,
                transport=self.transport,
            ) as client:
                response = await client.request(method, url, headers=headers, json=json_body)
        except httpx.HTTPError as exc:
            raise BrowserRuntimeError(f"{error_prefix}: {exc}") from exc
        if response.status_code >= 400:
            raise BrowserRuntimeError(f"{error_prefix}: HTTP {response.status_code} {response.text[:500]}")
        return response


class BrowserbaseCloudProvider(CloudBrowserProvider):
    backend = "browserbase"
    display_name = "Browserbase"

    def __init__(
        self,
        *,
        api_key: str = "",
        project_id: str = "",
        base_url: str = "https://api.browserbase.com",
        proxies: bool = True,
        advanced_stealth: bool = False,
        keep_alive: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        super().__init__(transport=transport)
        self.api_key = _first_text(api_key, os.getenv("BROWSERBASE_API_KEY"))
        self.project_id = _first_text(project_id, os.getenv("BROWSERBASE_PROJECT_ID"))
        self.base_url = _clean_base_url(_first_text(base_url, os.getenv("BROWSERBASE_BASE_URL")), "https://api.browserbase.com")
        self.proxies = bool(proxies)
        self.advanced_stealth = bool(advanced_stealth)
        self.keep_alive = bool(keep_alive)

    def is_configured(self) -> bool:
        return bool(self.api_key and self.project_id)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.is_configured(),
            "api_key_configured": bool(self.api_key),
            "project_id": self.project_id,
            "base_url": self.base_url,
        }

    async def create_session(self, *, session_key: str, session_timeout: int, timeout: int) -> CloudBrowserSession:
        if not self.is_configured():
            raise BrowserRuntimeError("Browserbase requires browserbase_api_key and browserbase_project_id or BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID.")
        body: dict[str, Any] = {
            "projectId": self.project_id,
            "timeout": max(1, int(session_timeout or 300)) * 1000,
        }
        if self.keep_alive:
            body["keepAlive"] = True
        if self.proxies:
            body["proxies"] = True
        if self.advanced_stealth:
            body["browserSettings"] = {"advancedStealth": True}
        response = await self._request(
            "POST",
            f"{self.base_url}/v1/sessions",
            headers={"Content-Type": "application/json", "X-BB-API-Key": self.api_key},
            json_body=body,
            timeout=timeout,
            error_prefix="Failed to create Browserbase session",
        )
        payload = _json_object(response, "Browserbase session response")
        provider_session_id = _required_text(payload, "id", "Browserbase session id")
        cdp_url = _required_text(payload, "connectUrl", "Browserbase CDP URL")
        return CloudBrowserSession(
            provider_session_id=provider_session_id,
            cdp_url=cdp_url,
            expires_at=time.monotonic() + max(1, int(session_timeout or 300)),
        )

    async def close_session(self, provider_session_id: str, *, timeout: int) -> bool:
        if not self.is_configured() or not provider_session_id:
            return False
        try:
            await self._request(
                "POST",
                f"{self.base_url}/v1/sessions/{provider_session_id}",
                headers={"Content-Type": "application/json", "X-BB-API-Key": self.api_key},
                json_body={"projectId": self.project_id, "status": "REQUEST_RELEASE"},
                timeout=timeout,
                error_prefix="Failed to close Browserbase session",
            )
            return True
        except BrowserRuntimeError:
            return False


class BrowserUseCloudProvider(CloudBrowserProvider):
    backend = "browser-use"
    display_name = "Browser Use"

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "https://api.browser-use.com/api/v3",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        super().__init__(transport=transport)
        self.api_key = _first_text(api_key, os.getenv("BROWSER_USE_API_KEY"))
        self.base_url = _clean_base_url(_first_text(base_url, os.getenv("BROWSER_USE_BASE_URL")), "https://api.browser-use.com/api/v3")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.is_configured(),
            "api_key_configured": bool(self.api_key),
            "base_url": self.base_url,
        }

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "X-Browser-Use-API-Key": self.api_key}

    async def create_session(self, *, session_key: str, session_timeout: int, timeout: int) -> CloudBrowserSession:
        if not self.is_configured():
            raise BrowserRuntimeError("Browser Use requires browser_use_api_key or BROWSER_USE_API_KEY.")
        timeout_minutes = max(1, (max(1, int(session_timeout or 300)) + 59) // 60)
        response = await self._request(
            "POST",
            f"{self.base_url}/browsers",
            headers=self._headers(),
            json_body={"timeout": timeout_minutes},
            timeout=timeout,
            error_prefix="Failed to create Browser Use session",
        )
        payload = _json_object(response, "Browser Use session response")
        provider_session_id = _required_text(payload, "id", "Browser Use session id")
        cdp_url = str(payload.get("cdpUrl") or payload.get("connectUrl") or "").strip()
        if not cdp_url:
            raise BrowserRuntimeError("Browser Use session response did not include cdpUrl or connectUrl.")
        return CloudBrowserSession(
            provider_session_id=provider_session_id,
            cdp_url=cdp_url,
            expires_at=time.monotonic() + max(1, int(session_timeout or 300)),
        )

    async def close_session(self, provider_session_id: str, *, timeout: int) -> bool:
        if not self.is_configured() or not provider_session_id:
            return False
        try:
            await self._request(
                "PATCH",
                f"{self.base_url}/browsers/{provider_session_id}",
                headers=self._headers(),
                json_body={"action": "stop"},
                timeout=timeout,
                error_prefix="Failed to close Browser Use session",
            )
            return True
        except BrowserRuntimeError:
            return False


class FirecrawlCloudProvider(CloudBrowserProvider):
    backend = "firecrawl"
    display_name = "Firecrawl"

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "https://api.firecrawl.dev",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        super().__init__(transport=transport)
        self.api_key = _first_text(api_key, os.getenv("FIRECRAWL_API_KEY"))
        self.base_url = _clean_base_url(_first_text(base_url, os.getenv("FIRECRAWL_API_URL")), "https://api.firecrawl.dev")

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.is_configured(),
            "api_key_configured": bool(self.api_key),
            "base_url": self.base_url,
        }

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

    async def create_session(self, *, session_key: str, session_timeout: int, timeout: int) -> CloudBrowserSession:
        if not self.is_configured():
            raise BrowserRuntimeError("Firecrawl browser sessions require firecrawl_api_key or FIRECRAWL_API_KEY.")
        ttl = max(1, int(session_timeout or 300))
        response = await self._request(
            "POST",
            f"{self.base_url}/v2/browser",
            headers=self._headers(),
            json_body={"ttl": ttl},
            timeout=timeout,
            error_prefix="Failed to create Firecrawl browser session",
        )
        payload = _json_object(response, "Firecrawl browser session response")
        provider_session_id = _required_text(payload, "id", "Firecrawl browser session id")
        cdp_url = _required_text(payload, "cdpUrl", "Firecrawl CDP URL")
        return CloudBrowserSession(
            provider_session_id=provider_session_id,
            cdp_url=cdp_url,
            expires_at=time.monotonic() + ttl,
        )

    async def close_session(self, provider_session_id: str, *, timeout: int) -> bool:
        if not self.is_configured() or not provider_session_id:
            return False
        try:
            await self._request(
                "DELETE",
                f"{self.base_url}/v2/browser/{provider_session_id}",
                headers=self._headers(),
                timeout=timeout,
                error_prefix="Failed to close Firecrawl browser session",
            )
            return True
        except BrowserRuntimeError:
            return False


class AgentBrowserRuntime:
    """Small wrapper around the `agent-browser` CLI JSON interface."""

    def __init__(
        self,
        *,
        command_timeout: int = 30,
        session_timeout: int = 300,
        command: str | None = None,
        cdp_url: str | None = None,
        cloud_provider: CloudBrowserProvider | None = None,
    ):
        self.command_timeout = max(1, int(command_timeout or 30))
        self.session_timeout = max(1, int(session_timeout or 300))
        self.command = str(command or "").strip()
        self.cdp_url = str(cdp_url or "").strip()
        self.cloud_provider = cloud_provider
        self._cloud_sessions: dict[str, CloudBrowserSession] = {}

    async def run(self, *, session_key: str, command: str, args: list[str] | None = None, timeout: int | None = None) -> dict[str, Any]:
        backend_args = await self._backend_args(session_key)
        argv = [
            *self._command_prefix(),
            *backend_args,
            "--json",
            command,
            *(args or []),
        ]
        return await self._run_subprocess(argv, timeout or self.command_timeout)

    async def _backend_args(self, session_key: str) -> list[str]:
        if self.cdp_url:
            return ["--cdp", await self._resolve_cdp_url()]
        if self.cloud_provider is not None:
            return ["--cdp", await self._cloud_cdp_url(session_key)]
        return ["--session", _browser_session_name(session_key)]

    async def _resolve_cdp_url(self) -> str:
        return await resolve_cdp_url(self.cdp_url, timeout=self.command_timeout)

    async def _cloud_cdp_url(self, session_key: str) -> str:
        cache_key = _browser_session_name(session_key)
        now = time.monotonic()
        cached = self._cloud_sessions.get(cache_key)
        if cached is not None and cached.expires_at > now:
            return cached.cdp_url
        if cached is not None:
            await self.cloud_provider.close_session(cached.provider_session_id, timeout=self.command_timeout)
        session = await self.cloud_provider.create_session(
            session_key=cache_key,
            session_timeout=self.session_timeout,
            timeout=self.command_timeout,
        )
        self._cloud_sessions[cache_key] = session
        return session.cdp_url

    def _command_prefix(self) -> list[str]:
        if self.command:
            return [self.command]

        agent_browser = shutil.which("agent-browser") or _local_agent_browser_path()
        if agent_browser:
            return [agent_browser]

        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if npx:
            return [npx, "agent-browser"]

        raise BrowserRuntimeError(
            "agent-browser CLI was not found. Install it with `npm install` in the repo root "
            "or `npm install -g agent-browser && agent-browser install`."
        )

    async def _run_subprocess(self, argv: list[str], timeout: int) -> dict[str, Any]:
        try:
            env = os.environ.copy()
            env.setdefault("AGENT_BROWSER_IDLE_TIMEOUT_MS", str(self.session_timeout * 1000))
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise BrowserRuntimeError(str(exc)) from exc

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=max(1, timeout))
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"success": False, "error": f"browser command timed out after {timeout}s"}

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        payload = _parse_json_payload(stdout_text)
        if payload is not None:
            if proc.returncode and "success" not in payload:
                payload["success"] = False
            if stderr_text and "stderr" not in payload:
                payload["stderr"] = stderr_text[-1200:]
            return payload

        if proc.returncode:
            return {
                "success": False,
                "error": stderr_text or stdout_text or f"browser command exited with code {proc.returncode}",
            }
        return {"success": True, "output": stdout_text}


def _local_agent_browser_path() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    bin_dir = repo_root / "node_modules" / ".bin"
    for name in ("agent-browser.cmd", "agent-browser.exe", "agent-browser"):
        candidate = bin_dir / name
        if candidate.exists():
            return str(candidate)
    return ""


def cloud_provider_from_config(
    browser_config: Any,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CloudBrowserProvider | None:
    backend = str(getattr(browser_config, "backend", "agent-browser") or "agent-browser").strip()
    if backend == "browserbase":
        return BrowserbaseCloudProvider(
            api_key=getattr(browser_config, "browserbase_api_key", ""),
            project_id=getattr(browser_config, "browserbase_project_id", ""),
            base_url=getattr(browser_config, "browserbase_base_url", ""),
            proxies=getattr(browser_config, "browserbase_proxies", True),
            advanced_stealth=getattr(browser_config, "browserbase_advanced_stealth", False),
            keep_alive=getattr(browser_config, "browserbase_keep_alive", True),
            transport=transport,
        )
    if backend == "browser-use":
        return BrowserUseCloudProvider(
            api_key=getattr(browser_config, "browser_use_api_key", ""),
            base_url=getattr(browser_config, "browser_use_base_url", ""),
            transport=transport,
        )
    if backend == "firecrawl":
        return FirecrawlCloudProvider(
            api_key=getattr(browser_config, "firecrawl_api_key", ""),
            base_url=getattr(browser_config, "firecrawl_base_url", ""),
            transport=transport,
        )
    return None


def browser_cloud_status(browser_config: Any) -> dict[str, dict[str, Any]]:
    return {
        "browserbase": BrowserbaseCloudProvider(
            api_key=getattr(browser_config, "browserbase_api_key", ""),
            project_id=getattr(browser_config, "browserbase_project_id", ""),
            base_url=getattr(browser_config, "browserbase_base_url", ""),
            proxies=getattr(browser_config, "browserbase_proxies", True),
            advanced_stealth=getattr(browser_config, "browserbase_advanced_stealth", False),
            keep_alive=getattr(browser_config, "browserbase_keep_alive", True),
        ).status(),
        "browser-use": BrowserUseCloudProvider(
            api_key=getattr(browser_config, "browser_use_api_key", ""),
            base_url=getattr(browser_config, "browser_use_base_url", ""),
        ).status(),
        "firecrawl": FirecrawlCloudProvider(
            api_key=getattr(browser_config, "firecrawl_api_key", ""),
            base_url=getattr(browser_config, "firecrawl_base_url", ""),
        ).status(),
    }


def _browser_session_name(session_key: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_key or "default")).strip("_")
    return f"opensprite_{normalized or 'default'}"[:80]


def _parse_json_payload(text: str) -> dict[str, Any] | None:
    for line in reversed([line.strip() for line in str(text or "").splitlines() if line.strip()]):
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        return payload if isinstance(payload, dict) else None
    return None


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _clean_base_url(value: str, default: str) -> str:
    return (str(value or "").strip() or default).rstrip("/")


def _json_object(response: httpx.Response, label: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise BrowserRuntimeError(f"{label} was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise BrowserRuntimeError(f"{label} was not a JSON object.")
    return payload


def _required_text(payload: dict[str, Any], key: str, label: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise BrowserRuntimeError(f"{label} was missing from provider response.")
    return value


async def resolve_cdp_url(raw_url: str, *, timeout: int = 30) -> str:
    """Resolve an HTTP CDP discovery URL into a browser WebSocket URL when possible."""
    raw = str(raw_url or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith(("ws://", "wss://")) and "/devtools/browser/" in lowered:
        return raw
    discovery_url = _cdp_discovery_url(raw)
    if not discovery_url:
        return raw
    try:
        async with httpx.AsyncClient(timeout=max(1, int(timeout or 30)), follow_redirects=True) as client:
            response = await client.get(discovery_url)
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return raw
    if isinstance(payload, dict):
        ws_url = str(payload.get("webSocketDebuggerUrl") or "").strip()
        if ws_url:
            return ws_url
    return raw


def _cdp_discovery_url(raw_url: str) -> str:
    raw = str(raw_url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        return raw if parsed.path.endswith("/json/version") else raw.rstrip("/") + "/json/version"
    if parsed.scheme in {"ws", "wss"} and parsed.netloc and not parsed.path.strip("/"):
        scheme = "http" if parsed.scheme == "ws" else "https"
        return f"{scheme}://{parsed.netloc}/json/version"
    return ""
