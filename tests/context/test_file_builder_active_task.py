from opensprite.context.file_builder import FileContextBuilder
from opensprite.documents.active_task import create_active_task_store


def test_file_builder_includes_active_task_when_session_has_one(tmp_path):
    builder = FileContextBuilder(
        app_home=tmp_path / "home",
        bootstrap_dir=tmp_path / "bootstrap",
        memory_dir=tmp_path / "memory",
        tool_workspace=tmp_path / "workspace",
        default_skills_dir=tmp_path / "skills",
    )

    task = create_active_task_store(builder.app_home, "telegram:room-1", workspace_root=builder.tool_workspace)
    task.write_managed_block(
        "- Status: active\n"
        "- Goal: Keep the agent on task\n"
        "- Deliverable: stable execution\n"
        "- Definition of done:\n"
        "  - task stays focused\n"
        "- Constraints:\n"
        "  - minimal changes\n"
        "- Assumptions:\n"
        "  - short user input\n"
        "- Plan:\n"
        "  1. record the task\n"
        "  2. follow the next step\n"
        "- Current step: 1. record the task\n"
        "- Next step: 2. follow the next step\n"
        "- Completed steps:\n"
        "  - none\n"
        "- Open questions:\n"
        "  - none"
    )

    prompt = builder.build_system_prompt("telegram:room-1")

    assert "# Active Task" in prompt
    assert "Keep the agent on task" in prompt
    assert str(task.active_task_file.resolve()) in prompt
