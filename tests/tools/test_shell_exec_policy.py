"""exec policy: compound-background rewrite + Hermes-aligned foreground guards."""

import asyncio
from pathlib import Path

import pytest

from opensprite.tools.shell import (
    _foreground_exec_guidance,
    _rewrite_compound_background,
)


class TestCompoundBackgroundRewrite:
    def test_simple_and_background(self):
        assert _rewrite_compound_background("A && B &") == "A && { B & }"

    def test_or_background(self):
        assert _rewrite_compound_background("A || B &") == "A || { B & }"

    def test_chained_and(self):
        assert _rewrite_compound_background("A && B && C &") == "A && B && { C & }"

    def test_newline_resets_chain_state(self):
        cmd = "A && B\nC &"
        assert _rewrite_compound_background(cmd) == cmd

    def test_simple_background_unchanged(self):
        assert _rewrite_compound_background("sleep 5 &") == "sleep 5 &"

    def test_fd_redirect_with_trailing_bg(self):
        assert _rewrite_compound_background("cmd 2>&1 &") == "cmd 2>&1 &"

    def test_amp_gt_inside_compound_background(self):
        cmd = "A && B &>/dev/null &"
        assert _rewrite_compound_background(cmd) == "A && { B &>/dev/null & }"

    def test_idempotent(self):
        once = _rewrite_compound_background("A && B &")
        assert _rewrite_compound_background(once) == once


class TestForegroundGuidance:
    def test_blocks_trailing_ampersand(self):
        assert _foreground_exec_guidance("sleep 1 &") is not None

    def test_blocks_inline_ampersand(self):
        assert _foreground_exec_guidance("foo & bar") is not None

    def test_blocks_nohup(self):
        assert _foreground_exec_guidance("nohup python server.py") is not None

    def test_blocks_uvicorn(self):
        assert _foreground_exec_guidance("uvicorn app:app --host 0.0.0.0") is not None

    def test_allows_plain_echo(self):
        assert _foreground_exec_guidance("echo hello") is None

    def test_allows_uvicorn_help(self):
        assert _foreground_exec_guidance("uvicorn --help") is None


def test_exec_tool_returns_guidance_for_uvicorn(tmp_path):
    from opensprite.tools.shell import ExecTool

    tool = ExecTool(workspace=Path(tmp_path))
    result = asyncio.run(tool.execute(command="uvicorn app:app"))
    assert result.startswith("Error:")
    assert "long-lived" in result.lower() or "server" in result.lower()


def test_exec_tool_runs_echo_when_allowed(tmp_path):
    from opensprite.tools.shell import ExecTool

    tool = ExecTool(workspace=Path(tmp_path))
    result = asyncio.run(tool.execute(command="echo opensprite_exec_ok"))
    assert "opensprite_exec_ok" in result
    assert not result.startswith("Error:")
