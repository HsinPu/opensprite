from opensprite.context.file_builder import FileContextBuilder


def _builder(tmp_path):
    return FileContextBuilder(
        app_home=tmp_path / "home",
        bootstrap_dir=tmp_path / "bootstrap",
        memory_dir=tmp_path / "memory",
        tool_workspace=tmp_path / "workspace",
        default_skills_dir=tmp_path / "skills",
    )


def test_build_messages_adds_workspace_task_guidance_for_code_task(tmp_path):
    builder = _builder(tmp_path)

    messages = builder.build_messages(
        history=[],
        current_message="Please fix the failing pytest in tests/test_app.py",
        channel="web",
        chat_id="web:browser-1",
    )

    assert [message["role"] for message in messages] == ["system", "system", "user", "user"]
    assert messages[1]["content"].startswith("# Workspace Task Guidance")
    assert "inspect relevant files and search results first" in messages[1]["content"]
    assert "run focused verification" in messages[1]["content"]


def test_build_messages_adds_workspace_task_guidance_for_chinese_error_task(tmp_path):
    builder = _builder(tmp_path)

    messages = builder.build_messages(
        history=[],
        current_message="這個 build 報錯，幫我修復",
        channel="web",
        chat_id="web:browser-1",
    )

    assert messages[1]["role"] == "system"
    assert "# Workspace Task Guidance" in messages[1]["content"]


def test_build_messages_skips_workspace_task_guidance_for_plain_chat(tmp_path):
    builder = _builder(tmp_path)

    messages = builder.build_messages(
        history=[],
        current_message="你覺得這樣可以嗎？",
        channel="telegram",
        chat_id="telegram:room-1",
    )

    assert [message["role"] for message in messages] == ["system", "user", "user"]
    assert "# Workspace Task Guidance" not in messages[0]["content"]


def test_build_messages_adds_planning_mode_overlay_for_explicit_plan_only_request(tmp_path):
    builder = _builder(tmp_path)

    messages = builder.build_messages(
        history=[],
        current_message="先規劃不要動手，幫我整理 tests/test_app.py 這個修復方案",
        channel="web",
        chat_id="web:browser-1",
    )

    assert [message["role"] for message in messages] == ["system", "system", "system", "user", "user"]
    assert messages[1]["content"].startswith("# Workspace Task Guidance")
    assert messages[2]["content"].startswith("# Planning Mode")
    assert "MUST NOT edit files" in messages[2]["content"]
    assert "read-only planning mode" in messages[2]["content"]
