import asyncio
import sys

from opensprite.tools.verify import VerifyCommandResult, VerifyTool


def test_verify_python_compile_passes_valid_files(tmp_path):
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    tool = VerifyTool(workspace=tmp_path)

    result = asyncio.run(tool.execute(action="python_compile", path="."))

    assert result.startswith("Verification passed: python_compile")
    assert "Files checked: 1" in result


def test_verify_python_compile_reports_syntax_errors(tmp_path):
    (tmp_path / "bad.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    tool = VerifyTool(workspace=tmp_path)

    result = asyncio.run(tool.execute(action="python_compile", path="."))

    assert result.startswith("Error: Python compile verification failed")
    assert "bad.py" in result


def test_verify_rejects_paths_outside_workspace(tmp_path):
    tool = VerifyTool(workspace=tmp_path)

    result = asyncio.run(tool.execute(action="python_compile", path=".."))

    assert result.startswith("Error: Verification path is outside the workspace")


def test_verify_pytest_uses_focused_args(tmp_path):
    tool = VerifyTool(workspace=tmp_path)
    captured = {}

    async def fake_run_command(command, cwd, timeout):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        return VerifyCommandResult(command=command, cwd=cwd, exit_code=0, output="1 passed")

    tool._run_command = fake_run_command

    result = asyncio.run(
        tool.execute(action="pytest", pytest_args=["tests/test_sample.py::test_ok"], timeout=7)
    )

    assert captured["command"] == [sys.executable, "-m", "pytest", "tests/test_sample.py::test_ok"]
    assert captured["cwd"] == tmp_path.resolve(strict=False)
    assert captured["timeout"] == 7
    assert result.startswith("Verification passed: pytest")


def test_verify_web_build_uses_package_json_build_script(tmp_path):
    package_dir = tmp_path / "apps" / "web"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    tool = VerifyTool(workspace=tmp_path)
    captured = {}

    async def fake_run_command(command, cwd, timeout):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        return VerifyCommandResult(command=command, cwd=cwd, exit_code=0, output="built")

    tool._resolve_npm_executable = lambda: "npm"
    tool._run_command = fake_run_command

    result = asyncio.run(tool.execute(action="web_build", timeout=9))

    assert captured["command"] == ["npm", "run", "build"]
    assert captured["cwd"] == package_dir.resolve(strict=False)
    assert captured["timeout"] == 9
    assert result.startswith("Verification passed: web_build")
