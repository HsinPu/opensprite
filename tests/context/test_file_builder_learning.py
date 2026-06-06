from opensprite.agent.message_history import LearningLedger
from opensprite.context.file_builder import FileContextBuilder
from opensprite.context.paths import sync_templates


def test_file_builder_injects_relevant_learning_context_into_messages(tmp_path):
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)
    builder = FileContextBuilder(
        app_home=app_home,
        bootstrap_dir=app_home / "bootstrap",
        memory_dir=app_home / "memory",
        tool_workspace=app_home / "workspace",
    )
    ledger = LearningLedger()
    builder.set_learning_ledger(ledger)
    ledger.record_learning(
        "telegram:room-1",
        kind="skill",
        target_id="pytest-helper",
        summary="Reusable pytest workflow for updating assertions and running focused tests.",
        source_run_id="run-1",
    )

    messages = builder.build_messages([], "Please update pytest assertions", session_id="telegram:room-1", channel="telegram")

    system_messages = [message["content"] for message in messages if message["role"] == "system"]
    assert any("# Relevant Learned Context" in content for content in system_messages)
    assert any("pytest-helper" in content for content in system_messages)
