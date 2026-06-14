export function buildRunSummaryPath(runId, sessionId) {
  return `/api/runs/${encodeURIComponent(runId)}/summary?session_id=${encodeURIComponent(sessionId)}`;
}

export function buildRunTracePath(runId, sessionId) {
  return `/api/runs/${encodeURIComponent(runId)}?session_id=${encodeURIComponent(sessionId)}`;
}

export function buildRunFileChangeRevertPath(runId, sessionId, changeId) {
  return `/api/runs/${encodeURIComponent(runId)}/file-changes/${encodeURIComponent(changeId)}/revert?session_id=${encodeURIComponent(sessionId)}`;
}

export function buildWorktreeCleanupPath() {
  return "/api/worktrees/cleanup";
}

export function buildRunsPath(sessionId, limit) {
  return `/api/runs?session_id=${encodeURIComponent(sessionId)}&limit=${limit}`;
}

export function buildSessionDeletePath(sessionId) {
  return `/api/sessions?session_id=${encodeURIComponent(sessionId)}`;
}

export function buildSessionsClearPath(channel = "web") {
  return `/api/sessions?channel=${encodeURIComponent(channel)}`;
}
