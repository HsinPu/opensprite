from opensprite.context.paths import get_active_task_file, get_chat_skills_dir, get_chat_workspace, get_user_profile_file


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


def test_user_profile_file_is_stable_per_session_and_separates_sessions(tmp_path):
    app_home = tmp_path / "home"
    workspace_root = app_home / "workspace"

    profile_a_first = get_user_profile_file(
        app_home=app_home, chat_id="telegram:user-a", workspace_root=workspace_root
    )
    profile_a_second = get_user_profile_file(
        app_home=app_home, chat_id="telegram:user-a", workspace_root=workspace_root
    )
    profile_b = get_user_profile_file(app_home=app_home, chat_id="telegram:user-b", workspace_root=workspace_root)

    assert profile_a_first == profile_a_second
    assert profile_a_first != profile_b
    assert profile_a_first.parent != profile_b.parent
    assert profile_a_first.name == "USER.md"
    assert profile_a_first.parent == get_chat_workspace("telegram:user-a", workspace_root=workspace_root)


def test_active_task_file_is_stable_per_session_and_separates_sessions(tmp_path):
    app_home = tmp_path / "home"
    workspace_root = app_home / "workspace"

    task_a_first = get_active_task_file(
        app_home=app_home, chat_id="telegram:user-a", workspace_root=workspace_root
    )
    task_a_second = get_active_task_file(
        app_home=app_home, chat_id="telegram:user-a", workspace_root=workspace_root
    )
    task_b = get_active_task_file(app_home=app_home, chat_id="telegram:user-b", workspace_root=workspace_root)

    assert task_a_first == task_a_second
    assert task_a_first != task_b
    assert task_a_first.parent != task_b.parent
    assert task_a_first.name == "ACTIVE_TASK.md"
    assert task_a_first.parent == get_chat_workspace("telegram:user-a", workspace_root=workspace_root)
