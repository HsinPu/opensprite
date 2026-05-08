import asyncio
import json

import httpx
import pytest

from opensprite.agent.tool_registration import register_browser_tools
from opensprite.agent.task_artifact import build_task_artifact
from opensprite.config.schema import BrowserToolConfig, ToolsConfig
from opensprite.tools.browser import (
    BrowserClickTool,
    BrowserConsoleTool,
    BrowserNavigateTool,
    BrowserSnapshotTool,
    BrowserTypeTool,
)
from opensprite.tools.browser_runtime import (
    AgentBrowserRuntime,
    BrowserRuntimeError,
    BrowserUseCloudProvider,
    BrowserbaseCloudProvider,
    CloudBrowserSession,
    FirecrawlCloudProvider,
    cloud_provider_from_config,
)
from opensprite.tools.evidence import build_tool_evidence
from opensprite.tools.registry import ToolRegistry


class _FakeRuntime:
    def __init__(self):
        self.calls = []

    async def run(self, *, session_key, command, args=None, timeout=None):
        self.calls.append({"session_key": session_key, "command": command, "args": list(args or []), "timeout": timeout})
        return {"success": True, "data": {"command": command}}


class _FakeCloudProvider:
    def __init__(self):
        self.create_calls = []

    async def create_session(self, *, session_key, session_timeout, timeout):
        self.create_calls.append({"session_key": session_key, "session_timeout": session_timeout, "timeout": timeout})
        return CloudBrowserSession(
            provider_session_id="provider-session-1",
            cdp_url="ws://cloud.example/devtools/browser/abc",
            expires_at=999999999.0,
        )

    async def close_session(self, provider_session_id, *, timeout):
        return True


def test_browser_navigate_uses_current_session_and_open_command():
    runtime = _FakeRuntime()
    tool = BrowserNavigateTool(
        runtime=runtime,
        get_session_id=lambda: "web:browser-1",
        browser_config=BrowserToolConfig(enabled=True),
    )

    result = json.loads(asyncio.run(tool.execute(url="https://example.com")))

    assert result["success"] is True
    assert result["type"] == "browser_navigate"
    assert runtime.calls == [
        {
            "session_key": "web:browser-1",
            "command": "open",
            "args": ["https://example.com"],
            "timeout": 60,
        }
    ]


def test_browser_snapshot_uses_compact_mode_by_default():
    runtime = _FakeRuntime()
    tool = BrowserSnapshotTool(runtime=runtime, get_session_id=lambda: None)

    result = json.loads(asyncio.run(tool.execute()))

    assert result["type"] == "browser_snapshot"
    assert runtime.calls[0] == {"session_key": "default", "command": "snapshot", "args": ["-c"], "timeout": None}


def test_browser_click_and_type_normalize_refs():
    runtime = _FakeRuntime()

    click = BrowserClickTool(runtime=runtime, get_session_id=lambda: "s")
    fill = BrowserTypeTool(runtime=runtime, get_session_id=lambda: "s")

    asyncio.run(click.execute(ref="e2"))
    asyncio.run(fill.execute(ref="@e3", text="hello"))

    assert runtime.calls[0]["args"] == ["@e2"]
    assert runtime.calls[1]["args"] == ["@e3", "hello"]


def test_register_browser_tools_adds_mvp_tools():
    registry = ToolRegistry()

    register_browser_tools(registry, get_session_id=lambda: "session")

    assert {
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_scroll",
        "browser_back",
        "browser_console",
    }.issubset(set(registry.tool_names))


def test_register_browser_tools_skips_when_disabled():
    registry = ToolRegistry()

    register_browser_tools(registry, get_session_id=lambda: "session", tools_config=ToolsConfig())

    assert not any(name.startswith("browser_") for name in registry.tool_names)


def test_register_browser_tools_configures_cloud_provider_runtime():
    registry = ToolRegistry()

    register_browser_tools(
        registry,
        get_session_id=lambda: "session",
        tools_config=ToolsConfig(
            browser={
                "enabled": True,
                "backend": "firecrawl",
                "firecrawl_api_key": "fc-key",
            }
        ),
    )

    tool = registry.get("browser_navigate")
    assert isinstance(tool.runtime.cloud_provider, FirecrawlCloudProvider)


def test_browser_navigate_blocks_private_urls_by_default():
    runtime = _FakeRuntime()
    tool = BrowserNavigateTool(runtime=runtime, browser_config=BrowserToolConfig(enabled=True))

    result = json.loads(asyncio.run(tool.execute(url="http://127.0.0.1:8765")))

    assert result == {
        "type": "browser_navigate",
        "success": False,
        "error": "Blocked: URL targets a private or internal host.",
    }
    assert runtime.calls == []


def test_browser_navigate_allows_private_urls_when_configured():
    runtime = _FakeRuntime()
    tool = BrowserNavigateTool(
        runtime=runtime,
        browser_config=BrowserToolConfig(enabled=True, allow_private_urls=True),
    )

    result = json.loads(asyncio.run(tool.execute(url="http://127.0.0.1:8765")))

    assert result["success"] is True
    assert runtime.calls[0]["args"] == ["http://127.0.0.1:8765"]


def test_browser_navigate_blocks_secret_bearing_urls():
    runtime = _FakeRuntime()
    tool = BrowserNavigateTool(runtime=runtime, browser_config=BrowserToolConfig(enabled=True))

    result = json.loads(asyncio.run(tool.execute(url="https://example.com/?api_key=secret-token-value")))

    assert result == {
        "type": "browser_navigate",
        "success": False,
        "error": "Blocked: URL appears to contain a secret or credential.",
    }
    assert runtime.calls == []


def test_agent_browser_runtime_builds_json_command(monkeypatch):
    runtime = AgentBrowserRuntime(command="agent-browser", command_timeout=9)
    captured = {}

    async def fake_run(argv, timeout):
        captured["argv"] = argv
        captured["timeout"] = timeout
        return {"success": True}

    monkeypatch.setattr(runtime, "_run_subprocess", fake_run)

    result = asyncio.run(runtime.run(session_key="web:browser-1", command="open", args=["https://example.com"]))

    assert result == {"success": True}
    assert captured == {
        "argv": [
            "agent-browser",
            "--session",
            "opensprite_web_browser-1",
            "--json",
            "open",
            "https://example.com",
        ],
        "timeout": 9,
    }


def test_agent_browser_runtime_uses_cdp_backend_without_session(monkeypatch):
    runtime = AgentBrowserRuntime(command="agent-browser", command_timeout=9, cdp_url="http://127.0.0.1:9222")
    captured = {}

    async def fake_resolve():
        return "ws://127.0.0.1:9222/devtools/browser/abc"

    async def fake_run(argv, timeout):
        captured["argv"] = argv
        captured["timeout"] = timeout
        return {"success": True}

    monkeypatch.setattr(runtime, "_resolve_cdp_url", fake_resolve)
    monkeypatch.setattr(runtime, "_run_subprocess", fake_run)

    result = asyncio.run(runtime.run(session_key="web:browser-1", command="open", args=["https://example.com"]))

    assert result == {"success": True}
    assert captured == {
        "argv": [
            "agent-browser",
            "--cdp",
            "ws://127.0.0.1:9222/devtools/browser/abc",
            "--json",
            "open",
            "https://example.com",
        ],
        "timeout": 9,
    }


def test_agent_browser_runtime_uses_cloud_provider_cdp_session(monkeypatch):
    cloud_provider = _FakeCloudProvider()
    runtime = AgentBrowserRuntime(
        command="agent-browser",
        command_timeout=9,
        session_timeout=600,
        cloud_provider=cloud_provider,
    )
    captured = []

    async def fake_run(argv, timeout):
        captured.append({"argv": argv, "timeout": timeout})
        return {"success": True}

    monkeypatch.setattr(runtime, "_run_subprocess", fake_run)

    asyncio.run(runtime.run(session_key="web:browser-1", command="open", args=["https://example.com"]))
    asyncio.run(runtime.run(session_key="web:browser-1", command="snapshot", args=["-c"]))

    assert cloud_provider.create_calls == [
        {"session_key": "opensprite_web_browser-1", "session_timeout": 600, "timeout": 9}
    ]
    assert captured[0] == {
        "argv": [
            "agent-browser",
            "--cdp",
            "ws://cloud.example/devtools/browser/abc",
            "--json",
            "open",
            "https://example.com",
        ],
        "timeout": 9,
    }
    assert captured[1]["argv"][:4] == [
        "agent-browser",
        "--cdp",
        "ws://cloud.example/devtools/browser/abc",
        "--json",
    ]


def test_cloud_provider_factory_uses_selected_browser_backend():
    provider = cloud_provider_from_config(
        BrowserToolConfig(
            enabled=True,
            backend="browser-use",
            browser_use_api_key="browser-use-key",
        )
    )

    assert isinstance(provider, BrowserUseCloudProvider)
    assert provider.is_configured() is True


def test_browserbase_provider_creates_and_closes_cdp_session():
    requests = []

    async def handler(request):
        requests.append(request)
        if request.method == "POST" and request.url.path == "/v1/sessions":
            return httpx.Response(200, json={"id": "bb-session-1", "connectUrl": "ws://browserbase/cdp"})
        if request.method == "POST" and request.url.path == "/v1/sessions/bb-session-1":
            return httpx.Response(204)
        return httpx.Response(404, text="not found")

    provider = BrowserbaseCloudProvider(
        api_key="bb-key",
        project_id="project-1",
        base_url="https://browserbase.test",
        transport=httpx.MockTransport(handler),
    )

    session = asyncio.run(provider.create_session(session_key="chat-1", session_timeout=300, timeout=9))
    closed = asyncio.run(provider.close_session(session.provider_session_id, timeout=9))

    create_body = json.loads(requests[0].content.decode("utf-8"))
    close_body = json.loads(requests[1].content.decode("utf-8"))
    assert session.provider_session_id == "bb-session-1"
    assert session.cdp_url == "ws://browserbase/cdp"
    assert closed is True
    assert requests[0].headers["X-BB-API-Key"] == "bb-key"
    assert create_body["projectId"] == "project-1"
    assert create_body["timeout"] == 300000
    assert close_body == {"projectId": "project-1", "status": "REQUEST_RELEASE"}


def test_browser_use_provider_creates_cdp_session():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "bu-session-1", "cdpUrl": "ws://browser-use/cdp"})

    provider = BrowserUseCloudProvider(
        api_key="bu-key",
        base_url="https://browser-use.test/api/v3",
        transport=httpx.MockTransport(handler),
    )

    session = asyncio.run(provider.create_session(session_key="chat-1", session_timeout=300, timeout=9))

    body = json.loads(requests[0].content.decode("utf-8"))
    assert session.provider_session_id == "bu-session-1"
    assert session.cdp_url == "ws://browser-use/cdp"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/api/v3/browsers"
    assert requests[0].headers["X-Browser-Use-API-Key"] == "bu-key"
    assert body == {"timeout": 5}


def test_firecrawl_provider_creates_cdp_session():
    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "fc-session-1", "cdpUrl": "ws://firecrawl/cdp"})

    provider = FirecrawlCloudProvider(
        api_key="fc-key",
        base_url="https://firecrawl.test",
        transport=httpx.MockTransport(handler),
    )

    session = asyncio.run(provider.create_session(session_key="chat-1", session_timeout=120, timeout=9))

    body = json.loads(requests[0].content.decode("utf-8"))
    assert session.provider_session_id == "fc-session-1"
    assert session.cdp_url == "ws://firecrawl/cdp"
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/v2/browser"
    assert requests[0].headers["Authorization"] == "Bearer fc-key"
    assert body == {"ttl": 120}


def test_browser_console_reads_or_evaluates_page_context():
    runtime = _FakeRuntime()
    tool = BrowserConsoleTool(runtime=runtime, browser_config=BrowserToolConfig(enabled=True))

    asyncio.run(tool.execute(clear=True))
    asyncio.run(tool.execute(expression="document.title"))

    assert runtime.calls[0]["command"] == "console"
    assert runtime.calls[0]["args"] == ["--clear"]
    assert runtime.calls[1]["command"] == "eval"
    assert runtime.calls[1]["args"] == ["document.title"]


def test_agent_browser_runtime_reports_missing_runtime(monkeypatch):
    monkeypatch.setattr("opensprite.tools.browser_runtime.shutil.which", lambda name: None)
    monkeypatch.setattr("opensprite.tools.browser_runtime._local_agent_browser_path", lambda: "")

    with pytest.raises(BrowserRuntimeError):
        AgentBrowserRuntime()._command_prefix()


def test_browser_navigation_evidence_builds_traceable_web_source_artifact():
    result = json.dumps(
        {
            "type": "browser_navigate",
            "success": True,
            "data": {
                "url": "https://example.com/docs",
                "title": "Example Docs",
                "snapshot": "Example documentation for browser automation.",
            },
        }
    )

    evidence = build_tool_evidence("browser_navigate", {"url": "https://example.com/docs"}, result, ok=True)
    artifact = build_task_artifact(evidence)

    assert artifact is not None
    assert artifact.kind == "web_source"
    assert artifact.source_tool == "browser_navigate"
    assert artifact.metadata["source_count"] == 1
    assert artifact.metadata["sources"][0]["url"] == "https://example.com/docs"
    assert artifact.metadata["sources"][0]["title"] == "Example Docs"
