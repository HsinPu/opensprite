import tomllib
from pathlib import Path


def test_runtime_dependencies_include_pytest_for_verify_tool():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]

    assert any(dependency.startswith("pytest") for dependency in dependencies)
