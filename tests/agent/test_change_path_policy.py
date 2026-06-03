from opensprite.agent.change_path_policy import (
    common_verification_path,
    is_python_file_path,
    is_python_test_path,
    is_web_app_path,
    path_requires_delegated_review,
    strip_repo_snapshot_prefix,
)


def test_path_requires_delegated_review_for_code_and_key_config_paths():
    assert path_requires_delegated_review("src/opensprite/runtime.py")
    assert path_requires_delegated_review("package.json")
    assert path_requires_delegated_review("snapshot_after/src/opensprite/app.vue")
    assert not path_requires_delegated_review("docs/usage.md")


def test_path_classification_helpers():
    assert is_web_app_path("apps/web/src/App.vue")
    assert not is_web_app_path("src/opensprite/channels/web.py")
    assert is_python_file_path("src/opensprite/runtime.py")
    assert not is_python_file_path("apps/web/src/App.vue")
    assert is_python_test_path("tests/agent/test_completion_gate.py")
    assert not is_python_test_path("src/opensprite/runtime.py")


def test_strip_repo_snapshot_prefix():
    assert strip_repo_snapshot_prefix("repo/src/opensprite/runtime.py") == "src/opensprite/runtime.py"


def test_common_verification_path_returns_shared_parent():
    assert common_verification_path(("src/opensprite/runtime.py", "src/opensprite/agent.py")) == "src/opensprite"
    assert common_verification_path(("src/opensprite/runtime.py",)) == "src/opensprite"
