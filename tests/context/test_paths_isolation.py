from opensprite.context.paths import get_chat_skills_dir, get_chat_workspace


def test_chat_workspace_is_stable_per_session_and_separates_sessions(tmp_path):
    workspace_root = tmp_path / "workspace"

    workspace_a_first = get_chat_workspace("telegram:user-a", workspace_root=workspace_root)
    workspace_a_second = get_chat_workspace("telegram:user-a", workspace_root=workspace_root)
    workspace_b = get_chat_workspace("telegram:user-b", workspace_root=workspace_root)

    assert workspace_a_first == workspace_a_second
    assert workspace_a_first != workspace_b
    assert workspace_a_first.name != workspace_b.name


def test_chat_skills_dir_is_nested_under_the_same_session_workspace(tmp_path):
    workspace_root = tmp_path / "workspace"

    workspace = get_chat_workspace("telegram:user-a", workspace_root=workspace_root)
    skills_dir = get_chat_skills_dir("telegram:user-a", workspace_root=workspace_root)

    assert skills_dir.parent == workspace
    assert skills_dir.name == "skills"
