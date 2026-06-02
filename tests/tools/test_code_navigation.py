import asyncio
import json

from opensprite.tools.code_navigation import CodeNavigationTool
from opensprite.tools.permissions import ToolPermissionPolicy
from opensprite.tools.registry import ToolRegistry
from opensprite.tools.result_status import classify_tool_result_status


def _payload(result: str):
    return json.loads(result)


def test_code_navigation_returns_document_symbols(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("class Sprite:\n    pass\n\ndef render():\n    return Sprite()\n", encoding="utf-8")
    tool = CodeNavigationTool(workspace=tmp_path)

    result = _payload(asyncio.run(tool.execute(action="document_symbols", path="app.py")))

    assert result["action"] == "document_symbols"
    assert result["symbols"] == [
        {"name": "Sprite", "kind": "class", "path": "app.py", "line": 1},
        {"name": "render", "kind": "function", "path": "app.py", "line": 4},
    ]


def test_code_navigation_finds_workspace_symbols_and_definitions(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text(
        "export class Sprite {}\nexport function render() { return new Sprite(); }\n",
        encoding="utf-8",
    )
    tool = CodeNavigationTool(workspace=tmp_path)

    symbols = _payload(asyncio.run(tool.execute(action="workspace_symbols", path="src", symbol="Spri")))
    definitions = _payload(asyncio.run(tool.execute(action="go_to_definition", path="src", symbol="Sprite")))

    assert symbols["symbols"] == [{"name": "Sprite", "kind": "class", "path": "src/app.js", "line": 1}]
    assert definitions["definitions"] == [{"name": "Sprite", "kind": "class", "path": "src/app.js", "line": 1}]


def test_code_navigation_finds_references(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("class Sprite:\n    pass\n\nvalue = Sprite()\n", encoding="utf-8")
    tool = CodeNavigationTool(workspace=tmp_path)

    result = _payload(asyncio.run(tool.execute(action="references", path="src", symbol="Sprite")))

    assert result["references"] == [
        {"path": "src/app.py", "line": 1, "preview": "class Sprite:"},
        {"path": "src/app.py", "line": 4, "preview": "value = Sprite()"},
    ]


def test_code_navigation_rejects_external_path(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("class Secret: pass\n", encoding="utf-8")
    tool = CodeNavigationTool(workspace=workspace)

    result = asyncio.run(tool.execute(action="document_symbols", path="../outside.py"))

    status = classify_tool_result_status(result)
    assert status.ok is False
    assert status.error_type == "CodeNavigationToolError"
    assert status.category == "access_denied"
    assert "Access denied" in status.error


def test_code_navigation_rejects_document_symbols_on_directory(tmp_path):
    tool = CodeNavigationTool(workspace=tmp_path)

    result = asyncio.run(tool.execute(action="document_symbols", path="."))
    status = classify_tool_result_status(result)

    assert status.ok is False
    assert status.error_type == "ToolValidationError"
    assert status.category == "invalid_arguments"
    assert status.invalid_arguments is True
    assert "requires a file path" in status.error


def test_code_navigation_requires_symbol_for_references(tmp_path):
    tool = CodeNavigationTool(workspace=tmp_path)

    result = asyncio.run(tool.execute(action="references", path=".", symbol=""))
    status = classify_tool_result_status(result)

    assert status.ok is False
    assert status.error_type == "ToolValidationError"
    assert status.category == "invalid_arguments"
    assert status.invalid_arguments is True
    assert "requires symbol" in status.error


def test_code_navigation_is_read_risk_tool():
    assert ToolPermissionPolicy.risk_levels_for_tool("code_navigation") == frozenset({"read"})

    registry = ToolRegistry(ToolPermissionPolicy(denied_risk_levels=["read"]))
    registry.register(CodeNavigationTool(workspace="."))

    assert "code_navigation" not in registry.tool_names
