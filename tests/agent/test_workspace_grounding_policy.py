from opensprite.agent.completion_gate import (
    WORKSPACE_CONTEXT_REFERENCE_MISSING_REASON,
    WORKSPACE_LOCATION_MISSING_REASON,
    contains_workspace_location_clue,
    response_references_workspace_path,
    workspace_paths,
)


def test_workspace_grounding_reasons_are_stable():
    assert (
        WORKSPACE_CONTEXT_REFERENCE_MISSING_REASON
        == "assistant final answer did not reference inspected workspace context"
    )
    assert WORKSPACE_LOCATION_MISSING_REASON == "assistant final answer did not identify the workspace location"


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


def test_workspace_location_clue_accepts_code_like_or_quoted_code_token():
    assert contains_workspace_location_clue("load_config handles it") is True
    assert contains_workspace_location_clue("Config.load handles it") is True
    assert contains_workspace_location_clue("check `AuthSettings`") is True


def test_workspace_location_clue_rejects_generic_answer():
    assert contains_workspace_location_clue("it is configured in the project") is False
