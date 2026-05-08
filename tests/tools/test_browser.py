import asyncio
import json

import pytest

from opensprite.agent.tool_registration import register_browser_tools
from opensprite.agent.task_artifact import build_task_artifact
from opensprite.config.schema import BrowserToolConfig, ToolsConfig
from opensprite.tools.browser import BrowserClickTool, BrowserNavigateTool, BrowserSnapshotTool, BrowserTypeTool
from opensprite.tools.browser_runtime import AgentBrowserRuntime, BrowserRuntimeError
from opensprite.tools.evidence import build_tool_evidence
from opensprite.tools.registry import ToolRegistry


class _FakeRuntime:
    def __init__(self):
        self.calls = []

    async def run(self, *, session_key, command, args=None, timeout=None):
        self.calls.append({"session_key": session_key, "command": command, "args": list(args or []), "timeout": timeout})
        return {"success": True, "data": {"command": command}}


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
    }.issubset(set(registry.tool_names))


def test_register_browser_tools_skips_when_disabled():
    registry = ToolRegistry()

    register_browser_tools(registry, get_session_id=lambda: "session", tools_config=ToolsConfig())

    assert not any(name.startswith("browser_") for name in registry.tool_names)


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
