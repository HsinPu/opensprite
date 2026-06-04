from opensprite.agent.worktree import (
    GIT_WORKTREE_ADD_FAILED_REASON,
    GIT_COMMAND_FAILED_REASON,
    GIT_WORKTREE_REMOVE_FAILED_REASON,
    MISSING_WORKTREE_MARKER_REASON,
    REPOSITORY_ROOT_MISSING_REASON,
    WORKSPACE_NOT_GIT_REPOSITORY_REASON,
    WORKTREE_MARKER_NOT_MANAGED_REASON,
    WORKTREE_SANDBOX_DISABLED_REASON,
    WORKTREE_SANDBOX_EXISTS_REASON,
    WorktreeSandboxInspector,
)


def test_worktree_sandbox_reasons_are_stable(tmp_path):
    assert WORKTREE_SANDBOX_DISABLED_REASON == "worktree sandbox is disabled"
    assert WORKSPACE_NOT_GIT_REPOSITORY_REASON == "workspace is not inside a git repository"
    assert WORKTREE_SANDBOX_EXISTS_REASON == "worktree sandbox already exists"
    assert GIT_WORKTREE_ADD_FAILED_REASON == "git worktree add failed"
    assert GIT_COMMAND_FAILED_REASON == "git command failed"
    assert MISSING_WORKTREE_MARKER_REASON == "missing OpenSprite worktree marker"
    assert WORKTREE_MARKER_NOT_MANAGED_REASON == "worktree marker is not managed by OpenSprite"
    assert REPOSITORY_ROOT_MISSING_REASON == "repository root no longer exists"
    assert GIT_WORKTREE_REMOVE_FAILED_REASON == "git worktree remove failed"

    metadata = WorktreeSandboxInspector(enabled=False, workspace_root=tmp_path).inspect()
    assert metadata.reason == WORKTREE_SANDBOX_DISABLED_REASON

    cleanup = WorktreeSandboxInspector.cleanup(tmp_path / "missing")
    assert cleanup["reason"] == MISSING_WORKTREE_MARKER_REASON
