from opensprite.agent.command_version_policy import command_inspects_git_repository_state


def test_command_inspects_git_repository_state_detects_repo_state_commands():
    assert command_inspects_git_repository_state("git rev-parse HEAD")
    assert command_inspects_git_repository_state("git status --short")
    assert command_inspects_git_repository_state("git log -1")


def test_command_inspects_git_repository_state_ignores_direct_version_commands():
    assert not command_inspects_git_repository_state("git --version")
    assert not command_inspects_git_repository_state("python --version")
