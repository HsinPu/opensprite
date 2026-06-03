from opensprite.agent.workspace_grounding_policy import (
    contains_workspace_location_clue,
    response_references_workspace_path,
    workspace_paths,
)


def test_workspace_location_clue_accepts_path_supplied_by_caller():
    assert contains_workspace_location_clue("the answer names a path", has_workspace_path=True) is True


def test_workspace_paths_extracts_unique_workspace_like_paths():
    assert workspace_paths("Inspect src/opensprite/runtime.py and pyproject.toml, then runtime.py again") == (
        "src/opensprite/runtime.py",
        "pyproject.toml",
        "runtime.py",
    )


def test_response_references_workspace_path_by_full_path_or_filename():
    assert response_references_workspace_path("src/opensprite/runtime.py", "see src/opensprite/runtime.py")
    assert response_references_workspace_path("src/opensprite/runtime.py", "the relevant file is runtime.py")
    assert not response_references_workspace_path("src/opensprite/runtime.py", "the relevant file is app.py")


def test_workspace_location_clue_accepts_symbol_or_quoted_code_token():
    assert contains_workspace_location_clue("function load_config handles it") is True
    assert contains_workspace_location_clue("check `AuthSettings`") is True


def test_workspace_location_clue_rejects_generic_answer():
    assert contains_workspace_location_clue("it is configured in the project") is False
