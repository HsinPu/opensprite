import asyncio
import json
from pathlib import Path

from opensprite.context.paths import sync_templates
from opensprite.tools.subagent_config import ConfigureSubagentTool

_VALID_DESCRIPTION = (
    "Chat-scoped helper: applies a repeatable workflow for tasks tied to this conversation only. "
    "Use when the user asks for the same multi-step process within this chat workspace."
)
_VALID_BODY = (
    "# Instructions\n\n"
    "Do the thing with care. Follow project conventions and prefer small, focused edits.\n"
)


def _session_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "chat-ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def test_configure_subagent_list_and_add(tmp_path):
    app_home = tmp_path / "opensprite-home"
    session_ws = _session_workspace(tmp_path)
    tool = ConfigureSubagentTool(app_home=app_home, workspace_resolver=lambda: session_ws)

    listed = asyncio.run(tool.execute(action="list"))
    payload = json.loads(listed)
    assert "subagent_prompts_dir" in payload
    assert "app_home_subagent_prompts_dir" in payload
    assert "subagents" in payload
    assert Path(payload["subagent_prompts_dir"]) == session_ws / "subagent_prompts"

    denied = asyncio.run(
        tool.execute(
            action="add",
            subagent_id="my-reviewer",
            description=_VALID_DESCRIPTION,
            body=_VALID_BODY,
        )
    )
    assert "user_confirmed" in denied.lower()

    out = asyncio.run(
        tool.execute(
            action="add",
            subagent_id="my-reviewer",
            description=_VALID_DESCRIPTION,
            body=_VALID_BODY,
            user_confirmed=True,
        )
    )
    assert "Added session subagent" in out

    dup = asyncio.run(
        tool.execute(
            action="add",
            subagent_id="my-reviewer",
            description=_VALID_DESCRIPTION,
            body=_VALID_BODY,
            user_confirmed=True,
        )
    )
    assert "already exists" in dup.lower()

    got = asyncio.run(tool.execute(action="get", subagent_id="my-reviewer"))
    data = json.loads(got)
    assert data["subagent_id"] == "my-reviewer"
    assert "my-reviewer" in data["content"]
    assert "---" in data["content"]


def test_configure_subagent_upsert_then_remove(tmp_path):
    app_home = tmp_path / "oh"
    session_ws = _session_workspace(tmp_path)
    tool = ConfigureSubagentTool(app_home=app_home, workspace_resolver=lambda: session_ws)

    asyncio.run(
        tool.execute(
            action="add",
            subagent_id="doc-writer",
            description=_VALID_DESCRIPTION,
            body=_VALID_BODY,
            user_confirmed=True,
        )
    )
    upsert_desc = (
        _VALID_DESCRIPTION
        + " Additional coverage for template reuse when migrating documents between environments "
        "and coordinating edits with upstream reviewers before publication."
    )
    up = asyncio.run(
        tool.execute(
            action="upsert",
            subagent_id="doc-writer",
            description=upsert_desc,
            body=_VALID_BODY + "\n\nSecond paragraph keeps the body substantive and long enough always.\n",
        )
    )
    assert "Updated" in up or "session subagent" in up

    rm = asyncio.run(tool.execute(action="remove", subagent_id="doc-writer"))
    assert "Removed session" in rm

    again = asyncio.run(tool.execute(action="remove", subagent_id="doc-writer"))
    assert "no session-managed" in again.lower()


def test_configure_subagent_add_refuses_when_prompt_exists_under_app_home(tmp_path):
    """writer exists under app home after sync; add must require upsert for a session file."""
    app_home = tmp_path / "home-with-writer"
    sync_templates(app_home, silent=True)
    session_ws = _session_workspace(tmp_path)
    tool = ConfigureSubagentTool(app_home=app_home, workspace_resolver=lambda: session_ws)
    asyncio.run(tool.execute(action="list"))

    out = asyncio.run(
        tool.execute(
            action="add",
            subagent_id="writer",
            description=_VALID_DESCRIPTION,
            body=_VALID_BODY,
        )
    )
    assert "upsert" in out.lower()


def test_configure_subagent_requires_workspace_resolver(tmp_path):
    tool = ConfigureSubagentTool(app_home=tmp_path / "h", workspace_resolver=None)
    out = asyncio.run(tool.execute(action="list"))
    assert "workspace_resolver" in out.lower()
