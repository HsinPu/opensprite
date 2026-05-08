"""Browser automation tools backed by local agent-browser."""

from __future__ import annotations

import json
from typing import Any, Callable

from .base import Tool
from .browser_runtime import AgentBrowserRuntime, BrowserRuntimeError
from .validation import NON_EMPTY_STRING_PATTERN


SessionIdGetter = Callable[[], str | None]


class BrowserToolBase(Tool):
    """Shared helpers for browser tools."""

    def __init__(self, *, runtime: AgentBrowserRuntime | None = None, get_session_id: SessionIdGetter | None = None):
        self.runtime = runtime or AgentBrowserRuntime()
        self.get_session_id = get_session_id or (lambda: None)

    def _session_key(self) -> str:
        return self.get_session_id() or "default"

    async def _run_browser(self, action: str, args: list[str] | None = None, *, timeout: int | None = None) -> str:
        try:
            payload = await self.runtime.run(
                session_key=self._session_key(),
                command=action,
                args=args or [],
                timeout=timeout,
            )
        except BrowserRuntimeError as exc:
            payload = {"success": False, "error": str(exc)}
        payload.setdefault("type", self.name)
        return json.dumps(payload, ensure_ascii=False)


class BrowserNavigateTool(BrowserToolBase):
    @property
    def name(self) -> str:
        return "browser_navigate"

    @property
    def description(self) -> str:
        return "Navigate the browser to a URL using the local agent-browser backend."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                    "description": "HTTP or HTTPS URL to open in the current browser session.",
                }
            },
            "required": ["url"],
        }

    async def _execute(self, **kwargs: Any) -> str:
        return await self._run_browser("open", [str(kwargs["url"])], timeout=60)


class BrowserSnapshotTool(BrowserToolBase):
    @property
    def name(self) -> str:
        return "browser_snapshot"

    @property
    def description(self) -> str:
        return "Return a text accessibility snapshot of the current browser page."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "full": {
                    "type": "boolean",
                    "description": "Return the full snapshot instead of a compact snapshot.",
                    "default": False,
                }
            },
        }

    async def _execute(self, **kwargs: Any) -> str:
        args = [] if bool(kwargs.get("full", False)) else ["-c"]
        return await self._run_browser("snapshot", args)


class BrowserClickTool(BrowserToolBase):
    @property
    def name(self) -> str:
        return "browser_click"

    @property
    def description(self) -> str:
        return "Click an element reference from browser_snapshot, such as @e3."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                    "description": "Element reference from browser_snapshot, for example @e3.",
                }
            },
            "required": ["ref"],
        }

    async def _execute(self, **kwargs: Any) -> str:
        ref = _normalize_ref(str(kwargs["ref"]))
        return await self._run_browser("click", [ref])


class BrowserTypeTool(BrowserToolBase):
    @property
    def name(self) -> str:
        return "browser_type"

    @property
    def description(self) -> str:
        return "Fill text into an element reference from browser_snapshot."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                    "description": "Input element reference from browser_snapshot, for example @e5.",
                },
                "text": {"type": "string", "description": "Text to enter into the target element."},
            },
            "required": ["ref", "text"],
        }

    async def _execute(self, **kwargs: Any) -> str:
        return await self._run_browser("fill", [_normalize_ref(str(kwargs["ref"])), str(kwargs["text"])])


class BrowserPressTool(BrowserToolBase):
    @property
    def name(self) -> str:
        return "browser_press"

    @property
    def description(self) -> str:
        return "Press a keyboard key in the current browser page, such as Enter or Tab."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "pattern": NON_EMPTY_STRING_PATTERN,
                    "description": "Keyboard key to press, for example Enter, Tab, Escape, or ArrowDown.",
                }
            },
            "required": ["key"],
        }

    async def _execute(self, **kwargs: Any) -> str:
        return await self._run_browser("press", [str(kwargs["key"])])


class BrowserScrollTool(BrowserToolBase):
    @property
    def name(self) -> str:
        return "browser_scroll"

    @property
    def description(self) -> str:
        return "Scroll the current browser page up or down."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction.",
                }
            },
            "required": ["direction"],
        }

    async def _execute(self, **kwargs: Any) -> str:
        return await self._run_browser("scroll", [str(kwargs["direction"]), "500"])


class BrowserBackTool(BrowserToolBase):
    @property
    def name(self) -> str:
        return "browser_back"

    @property
    def description(self) -> str:
        return "Navigate back in the current browser page history."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def _execute(self, **kwargs: Any) -> str:
        return await self._run_browser("back", [])


def _normalize_ref(ref: str) -> str:
    normalized = str(ref or "").strip()
    return normalized if normalized.startswith("@") else f"@{normalized}"
