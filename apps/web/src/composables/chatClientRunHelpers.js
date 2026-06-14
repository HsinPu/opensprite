import { normalizeTraceEventCounts } from "./runTraceNormalizers";

function coerceNonNegativeInteger(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) {
    return 0;
  }
  return Math.trunc(number);
}

export function createRunViewState({ runId, sessionId, status = "running", createdAt, updatedAt = createdAt, finishedAt = null }) {
  return {
    runId,
    sessionId,
    status,
    createdAt,
    updatedAt,
    finishedAt,
    events: [],
    rawEvents: [],
    eventCounts: normalizeTraceEventCounts(null, []),
    parts: [],
    artifacts: [],
    fileChanges: [],
    diffSummary: null,
    worktreeSandbox: null,
    summary: null,
    summaryLoading: false,
    summaryError: "",
    summaryNotFoundAttempts: 0,
    traceLoaded: false,
    traceLoading: false,
    traceError: "",
  };
}

export function statusFromRunEvent(eventType, payload, eventStatus = "") {
  if (eventType === "run_started") {
    return "running";
  }
  if (eventType === "run_finished") {
    return payload.status || eventStatus || "completed";
  }
  if (eventType === "run_failed") {
    return payload.status || eventStatus || "failed";
  }
  if (eventType === "run_cancelled") {
    return payload.status || eventStatus || "cancelled";
  }
  if (eventType === "run_cancel_requested") {
    return payload.status || eventStatus || "cancelling";
  }
  return null;
}

export function formatRunFinishDetail(payload, copy) {
  const parts = [];
  if (Number.isFinite(Number(payload.executed_tool_calls))) {
    parts.push(copy.run.toolCalls(payload.executed_tool_calls));
  }
  if (Number.isFinite(Number(payload.context_compactions)) && Number(payload.context_compactions) > 0) {
    parts.push(copy.run.compactions(payload.context_compactions));
  }
  if (payload.had_tool_error) {
    parts.push(copy.run.toolWarning);
  }
  return parts.join(" · ");
}

export function formatSubagentDetail(payload) {
  return [payload.prompt_type || payload.promptType, payload.task_id || payload.taskId].filter(Boolean).join(" · ");
}

export function formatSubagentGroupDetail(payload) {
  const summary = String(payload.summary || payload.message || payload.error || "").trim();
  if (summary) {
    return summary;
  }
  const total = coerceNonNegativeInteger(payload.total_tasks ?? payload.totalTasks);
  return total > 0 ? `${total} task(s)` : "";
}

export function formatWorkflowDetail(payload) {
  return String(payload.summary || payload.error || payload.task_preview || payload.message || payload.workflow || "").trim();
}

export function formatWorkflowStepDetail(payload) {
  return String(payload.summary || payload.error || payload.task_preview || payload.label || "").trim();
}

export function formatAutoContinueDetail(payload) {
  const workflow = String(payload.direct_workflow || payload.directWorkflow || "").trim();
  const startStep = String(payload.direct_start_step || payload.directStartStep || "").trim();
  const verifyAction = String(payload.direct_verify_action || payload.directVerifyAction || "").trim();
  const verifyPath = String(payload.direct_verify_path || payload.directVerifyPath || "").trim();
  if (workflow && startStep) {
    return `workflow resume: ${workflow} -> ${startStep}`;
  }
  if (verifyAction) {
    return verifyPath ? `verification: ${verifyAction} (${verifyPath})` : `verification: ${verifyAction}`;
  }
  return String(payload.reason || payload.completion_reason || payload.completionReason || "").trim();
}
