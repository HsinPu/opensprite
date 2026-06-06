from opensprite.agent.quality_gate import (
    COMMAND_VERSION_MISSING_REASON,
    command_inspects_git_repository_state,
    command_version_follow_up_instruction,
    command_version_missing_detail,
)


def test_command_inspects_git_repository_state_detects_repo_state_commands():
    assert command_inspects_git_repository_state("git rev-parse HEAD")
    assert command_inspects_git_repository_state("git status --short")
    assert command_inspects_git_repository_state("git log -1")


def test_command_inspects_git_repository_state_ignores_direct_version_commands():
    assert not command_inspects_git_repository_state("git --version")
    assert not command_inspects_git_repository_state("python --version")


def test_command_version_follow_up_instruction_prefers_direct_version_command():
    instruction = command_version_follow_up_instruction()

    assert "<command> --version" in instruction
    assert "Do not inspect `.git`" in instruction


def test_command_version_missing_detail_distinguishes_repo_state_confusion():
    confused = command_version_missing_detail(inspected_repository_state=True)
    generic = command_version_missing_detail(inspected_repository_state=False)

    assert COMMAND_VERSION_MISSING_REASON == "command version answer did not report a version"
    assert "instead of inspecting `.git`" in confused
    assert "clearly state that the command is unavailable" in generic
