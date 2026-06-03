"""write_file / edit_file refuse OpenSprite main and split JSON config paths."""

import asyncio
import json

from opensprite.tools.filesystem import ApplyPatchTool, EditFileTool, WriteFileTool
from opensprite.tools.result_status import classify_tool_result_status


def test_write_file_blocks_opensprite_json_basename(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)
    out = asyncio.run(tool.execute(path="opensprite.json", content="{}"))
    status = classify_tool_result_status(out)
    assert status.ok is False
    assert status.error_type == "ToolGuardrailError"
    assert status.category == "protected_config"
    assert "configuration files" in out.lower()
    assert "cannot modify" in out.lower()
    assert not (tmp_path / "opensprite.json").exists()


def test_write_file_blocks_opensprite_json_case_insensitive(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)
    out = asyncio.run(tool.execute(path="OpenSprite.JSON", content="{}"))
    assert "cannot modify" in out.lower()
    assert not any(p.name.lower() == "opensprite.json" for p in tmp_path.iterdir() if p.is_file())


def test_write_file_blocks_resolved_main_config_path(tmp_path):
    cfg = tmp_path / "my_settings.json"
    cfg.write_text("{}", encoding="utf-8")
    tool = WriteFileTool(
        workspace=tmp_path,
        config_path_resolver=lambda: cfg,
    )
    out = asyncio.run(tool.execute(path="my_settings.json", content='{"x":1}'))
    assert "configuration file" in out.lower() or "cannot modify" in out.lower()
    assert cfg.read_text() == "{}"


def test_write_file_allows_other_json(tmp_path):
    tool = WriteFileTool(workspace=tmp_path, config_path_resolver=lambda: tmp_path / "my_settings.json")
    out = asyncio.run(tool.execute(path="notes.json", content="[]"))
    assert "Successfully wrote" in out
    assert (tmp_path / "notes.json").read_text() == "[]"


def test_write_file_blocks_channels_json_when_split_config(tmp_path):
    cfg_path = tmp_path / "opensprite.json"
    cfg_path.write_text(
        json.dumps(
            {
                "llm": {"temperature": 0.7, "max_tokens": 8192, "providers": {}},
                "storage": {"type": "memory", "path": ":memory:"},
                "channels": {"telegram": {"enabled": False, "token": ""}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "channels.json").write_text("{}", encoding="utf-8")
    tool = WriteFileTool(workspace=tmp_path, config_path_resolver=lambda: cfg_path)
    out = asyncio.run(tool.execute(path="channels.json", content='{"x": 1}'))
    assert "configuration files" in out.lower()
    assert (tmp_path / "channels.json").read_text() == "{}"


def test_write_file_blocks_custom_channels_file_name(tmp_path):
    cfg_path = tmp_path / "opensprite.json"
    cfg_path.write_text(
        json.dumps(
            {
                "llm": {"temperature": 0.7, "max_tokens": 8192, "providers": {}},
                "storage": {"type": "memory", "path": ":memory:"},
                "channels_file": "my_channels.json",
                "channels": {"telegram": {"enabled": False, "token": ""}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "my_channels.json").write_text("{}", encoding="utf-8")
    tool = WriteFileTool(workspace=tmp_path, config_path_resolver=lambda: cfg_path)
    out = asyncio.run(tool.execute(path="my_channels.json", content='{"x": 1}'))
    assert "configuration files" in out.lower()
    assert (tmp_path / "my_channels.json").read_text() == "{}"


def test_edit_file_blocks_opensprite_json(tmp_path):
    target = tmp_path / "opensprite.json"
    target.write_text('{"a":1}', encoding="utf-8")
    tool = EditFileTool(workspace=tmp_path)
    out = asyncio.run(
        tool.execute(path="opensprite.json", old_text='"a":1', new_text='"a":2')
    )
    assert "cannot modify" in out.lower()
    assert '"a":1' in target.read_text()


def test_write_file_blocks_sensitive_user_config_under_home(tmp_path, monkeypatch):
    monkeypatch.setattr("opensprite.tools.filesystem.Path.home", lambda: tmp_path)
    tool = WriteFileTool(workspace=tmp_path)

    out = asyncio.run(tool.execute(path=".ssh/authorized_keys", content="ssh-rsa AAA"))
    status = classify_tool_result_status(out)

    assert status.ok is False
    assert status.error_type == "ToolGuardrailError"
    assert status.category == "sensitive_user_config"
    assert "sensitive user configuration" in out.lower()
    assert not (tmp_path / ".ssh" / "authorized_keys").exists()


def test_apply_patch_blocks_sensitive_user_config_under_home(tmp_path, monkeypatch):
    monkeypatch.setattr("opensprite.tools.filesystem.Path.home", lambda: tmp_path)
    tool = ApplyPatchTool(workspace=tmp_path)

    out = asyncio.run(
        tool.execute(
            changes=[{"action": "add", "path": ".aws/credentials", "content": "[default]\n"}]
        )
    )
    status = classify_tool_result_status(out)

    assert status.ok is False
    assert status.error_type == "ToolGuardrailError"
    assert status.category == "sensitive_user_config"
    assert "sensitive user configuration" in out.lower()
    assert not (tmp_path / ".aws" / "credentials").exists()
