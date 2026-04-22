"""Tests for Unix exec background-line rewrite (pipe inheritance avoidance)."""

import sys
from unittest.mock import patch

from opensprite.tools.shell import _rewrite_background_command


def test_rewrite_skipped_on_windows():
    cmd = "uvicorn app:app &\nsleep 1\n"
    with patch.object(sys, "platform", "win32"):
        assert _rewrite_background_command(cmd) == cmd


def test_rewrite_single_line_with_setsid():
    with patch.object(sys, "platform", "linux"):
        with patch("opensprite.tools.shell.shutil.which", return_value="/usr/bin/setsid"):
            got = _rewrite_background_command("  uvicorn app:app --port 8000 &")
    assert got.startswith("  ")
    assert "/usr/bin/setsid" in got
    assert "sh -c " in got
    assert "</dev/null >/dev/null 2>&1 &" in got
    assert "uvicorn app:app --port 8000" in got
    assert got.endswith("&")
    assert "2>&1 &" in got


def test_rewrite_multiline_preserves_foreground():
    with patch.object(sys, "platform", "linux"):
        with patch("opensprite.tools.shell.shutil.which", return_value="/bin/setsid"):
            got = _rewrite_background_command(
                "uvicorn app:app &\nsleep 1\ncurl -s http://127.0.0.1:8000/\n"
            )
    lines = got.splitlines()
    assert "setsid" in lines[0] and "uvicorn" in lines[0]
    assert lines[1] == "sleep 1"
    assert lines[2] == "curl -s http://127.0.0.1:8000/"


def test_rewrite_skips_and_operator():
    cmd = "true && false\n"
    with patch.object(sys, "platform", "linux"):
        with patch("opensprite.tools.shell.shutil.which", return_value="/bin/setsid"):
            assert _rewrite_background_command(cmd) == cmd


def test_rewrite_falls_back_without_setsid():
    with patch.object(sys, "platform", "linux"):
        with patch("opensprite.tools.shell.shutil.which", return_value=None):
            got = _rewrite_background_command("echo hi &")
    assert got.startswith("sh -c ")
    assert "setsid" not in got
    assert "echo hi" in got
