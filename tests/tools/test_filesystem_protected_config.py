"""write_file / edit_file refuse opensprite.json and the resolved main config path."""

import asyncio

from opensprite.tools.filesystem import EditFileTool, WriteFileTool


def test_write_file_blocks_opensprite_json_basename(tmp_path):
    tool = WriteFileTool(workspace=tmp_path)
    out = asyncio.run(tool.execute(path="opensprite.json", content="{}"))
    assert "opensprite.json" in out.lower()
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


def test_edit_file_blocks_opensprite_json(tmp_path):
    target = tmp_path / "opensprite.json"
    target.write_text('{"a":1}', encoding="utf-8")
    tool = EditFileTool(workspace=tmp_path)
    out = asyncio.run(
        tool.execute(path="opensprite.json", old_text='"a":1', new_text='"a":2')
    )
    assert "cannot modify" in out.lower()
    assert '"a":1' in target.read_text()
