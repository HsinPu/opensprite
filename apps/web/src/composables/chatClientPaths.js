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

export function buildCuratorStatusPath(sessionId) {
  return `/api/curator/status?session_id=${encodeURIComponent(sessionId)}`;
}

export function buildCuratorHistoryPath(sessionId, limit) {
  return `/api/curator/history?session_id=${encodeURIComponent(sessionId)}&limit=${encodeURIComponent(limit)}`;
}

export function buildCuratorActionPath(action, sessionId, scope = "") {
  const params = new URLSearchParams({ session_id: sessionId });
  if (scope) {
    params.set("scope", scope);
  }
  return `/api/curator/${encodeURIComponent(action)}?${params.toString()}`;
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

export function buildBackgroundProcessesPath(sessionId = "", limit) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (sessionId) {
    params.set("session_id", sessionId);
  }
  return `/api/background-processes?${params.toString()}`;
}
