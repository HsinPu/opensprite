import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { getDisplayCopy } from "../i18n/copy";

const STORAGE_KEYS = {
  wsUrl: "opensprite:web:wsUrl",
  displayName: "opensprite:web:displayName",
  activeExternalChatId: "opensprite:web:activeExternalChatId",
  showRunTimeline: "opensprite:web:showRunTimeline",
  showRunSummary: "opensprite:web:showRunSummary",
  showRunTrace: "opensprite:web:showRunTrace",
  language: "opensprite:web:language",
  colorScheme: "opensprite:web:colorScheme",
  sidebarCollapsed: "opensprite:web:sidebarCollapsed",
};

const DEFAULT_LANGUAGE = "zh-TW";
const DEFAULT_COLOR_SCHEME = "system";
const SUPPORTED_LANGUAGES = new Set(["zh-TW", "en"]);
const SUPPORTED_COLOR_SCHEMES = new Set(["system", "light", "dark"]);
const LANGUAGE_ATTRIBUTES = {
  "zh-TW": "zh-Hant-TW",
  en: "en",
};

const MAX_RUN_EVENTS = 80;
const MAX_RUN_TEXT_EVENTS = 24;
const MAX_RUN_ARTIFACTS = 200;
const MAX_TIMELINE_EVENTS = 8;
const RUN_HISTORY_LIMIT = 10;
const RUN_SUMMARY_FETCH_DELAY_MS = 500;
const RUN_SUMMARY_NOT_FOUND_RETRY_DELAY_MS = 1200;
const RUN_SUMMARY_NOT_FOUND_RETRY_LIMIT = 3;
const RUN_BACKFILL_COOLDOWN_MS = 2000;
const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "cancelled"]);
const TERMINAL_PART_STATES = new Set(["completed", "failed", "cancelled", "error"]);
const RUN_EVENT_KINDS = new Set(["run", "llm", "tool", "verification", "permission", "work", "completion", "file", "text", "system", "other"]);
const MCP_TRANSPORT_TYPES = new Set(["stdio", "sse", "streamableHttp"]);
const TIMELINE_EVENT_TYPES = new Set([
  "run_started",
  "llm_status",
  "tool_started",
  "file_changed",
  "verification_started",
  "verification_result",
  "permission_requested",
  "permission_granted",
  "permission_denied",
  "run_finished",
  "run_failed",
  "run_cancelled",
  "run_cancel_requested",
]);

function resolveDefaultWsUrl() {
  if (window.location.protocol === "http:" || window.location.protocol === "https:") {
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProtocol}//${window.location.host}/ws`;
  }
  return "ws://127.0.0.1:8765/ws";
}

const DEFAULT_WS_URL = resolveDefaultWsUrl();

function readStoredValue(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function normalizeChoice(value, fallback, allowedValues) {
  const normalized = String(value || "").trim();
  return allowedValues.has(normalized) ? normalized : fallback;
}

function readStoredChoice(key, fallback, allowedValues) {
  return normalizeChoice(readStoredValue(key, fallback), fallback, allowedValues);
}

function getResolvedColorScheme(colorScheme) {
  if (colorScheme !== "system") {
    return colorScheme;
  }
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

function writeStoredValue(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    return;
  }
}

function readStoredBoolean(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    if (value === null) {
      return fallback;
    }
    return value === "true";
  } catch {
    return fallback;
  }
}

function randomToken() {
  return Math.random().toString(36).slice(2, 8);
}

function generateExternalChatId() {
  return `browser-${Date.now().toString(36)}-${randomToken()}`;
}

function externalChatIdFromSessionId(sessionId) {
  const normalized = String(sessionId || "").trim();
  const separatorIndex = normalized.indexOf(":");
  if (separatorIndex < 0) {
    return normalized;
  }
  return normalized.slice(separatorIndex + 1).trim();
}

function channelFromSessionId(sessionId) {
  const normalized = String(sessionId || "").trim();
  const separatorIndex = normalized.indexOf(":");
  return separatorIndex > 0 ? normalized.slice(0, separatorIndex).trim() : "web";
}

function isExternalChannelSessionId(value) {
  const normalized = String(value || "").trim();
  return normalized.includes(":") && channelFromSessionId(normalized) !== "web";
}

function summarizeTitle(text) {
  const singleLine = text.trim().replace(/\s+/g, " ");
  if (!singleLine) {
    return "New chat";
  }
  return singleLine.length > 30 ? `${singleLine.slice(0, 30)}...` : singleLine;
}

function makeMessage(role, text, meta) {
  return {
    id: `msg-${Date.now().toString(36)}-${randomToken()}`,
    role,
    text,
    meta,
    createdAt: Date.now(),
  };
}

function createSession(externalChatId) {
  return {
    externalChatId: externalChatId || generateExternalChatId(),
    transportExternalChatId: externalChatId || "",
    channel: "web",
    sessionId: null,
    title: "New chat",
    updatedAt: Date.now(),
    messages: [],
    entries: [],
    status: { status: "idle", updatedAt: Date.now(), metadata: {} },
    workState: null,
    activeRunId: null,
    runs: [],
    runsLoaded: false,
    runsLoading: false,
    runsError: "",
  };
}

function coerceStringList(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function coerceBoolean(value) {
  return value === true || value === "true" || value === 1;
}

function coerceNonNegativeInteger(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0) {
    return 0;
  }
  return Math.floor(number);
}

function normalizeRunKind(value, fallback = "other") {
  const normalized = String(value || "").trim();
  return RUN_EVENT_KINDS.has(normalized) ? normalized : fallback;
}

function inferRunEventKind(eventType) {
  const normalized = String(eventType || "").trim();
  if (normalized === "run_part_delta" || normalized === "message_part_delta") {
    return "text";
  }
  if (normalized.startsWith("run_") || normalized.startsWith("auto_continue.")) {
    return "run";
  }
  if (normalized.startsWith("llm_")) {
    return "llm";
  }
  if (normalized === "reasoning_delta") {
    return "llm";
  }
  if (normalized.startsWith("tool_")) {
    return "tool";
  }
  if (normalized.startsWith("verification_")) {
    return "verification";
  }
  if (normalized.startsWith("permission_")) {
    return "permission";
  }
  if (normalized.startsWith("work_") || normalized.startsWith("task_")) {
    return "work";
  }
  if (normalized === "file_changed") {
    return "file";
  }
  if (normalized === "completion_gate.evaluated") {
    return "completion";
  }
  return "other";
}

function inferRunEventStatus(eventType, payload = {}) {
  const normalized = String(eventType || "").trim();
  const explicit = String(payload.status || payload.state || "").trim();
  if (explicit) {
    return explicit;
  }
  if (normalized === "run_part_delta" || normalized === "message_part_delta") {
    return "running";
  }
  if (normalized === "run_started" || normalized.endsWith("_started") || normalized === "llm_status" || normalized === "auto_continue.scheduled") {
    return "running";
  }
  if (normalized === "run_failed") {
    return "failed";
  }
  if (normalized === "run_cancelled") {
    return "cancelled";
  }
  if (normalized === "run_cancel_requested") {
    return "cancelling";
  }
  if (payload.ok === false) {
    return inferRunEventKind(normalized) === "verification" ? "failed" : "error";
  }
  return "completed";
}

function isTextRunEvent(event) {
  const eventType = String(event?.eventType || event?.event_type || "").trim();
  return event?.kind === "text" || eventType === "run_part_delta" || eventType === "message_part_delta";
}

function compactRunEvents(events) {
  let textCount = 0;
  let otherCount = 0;
  const kept = [];
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (isTextRunEvent(event)) {
      if (textCount >= MAX_RUN_TEXT_EVENTS) {
        continue;
      }
      textCount += 1;
    } else {
      if (otherCount >= MAX_RUN_EVENTS) {
        continue;
      }
      otherCount += 1;
    }
    kept.push(event);
  }
  return kept.reverse();
}

function normalizeTraceEventCounts(counts, events = []) {
  const returned = coerceNonNegativeInteger(counts?.returned ?? events.length);
  const total = coerceNonNegativeInteger(counts?.total ?? returned);
  return {
    total,
    returned,
    compacted: coerceNonNegativeInteger(counts?.compacted ?? Math.max(0, total - returned)),
    textTotal: coerceNonNegativeInteger(counts?.text_total ?? counts?.textTotal),
    textReturned: coerceNonNegativeInteger(counts?.text_returned ?? counts?.textReturned),
    maxEvents: coerceNonNegativeInteger(counts?.max_events ?? counts?.maxEvents),
    maxTextEvents: coerceNonNegativeInteger(counts?.max_text_events ?? counts?.maxTextEvents),
  };
}

function updateLiveTraceEventCounts(run, event) {
  const previous = normalizeTraceEventCounts(run?.eventCounts, run?.rawEvents || []);
  const textTotal = previous.textTotal + (isTextRunEvent(event) ? 1 : 0);
  const textReturned = (run.rawEvents || []).filter(isTextRunEvent).length;
  run.eventCounts = {
    total: previous.total + 1,
    returned: (run.rawEvents || []).length,
    compacted: Math.max(0, previous.total + 1 - (run.rawEvents || []).length),
    textTotal,
    textReturned,
    maxEvents: MAX_RUN_EVENTS,
    maxTextEvents: MAX_RUN_TEXT_EVENTS,
  };
}

function normalizeRunArtifact(artifact, fallback = {}) {
  if (!artifact || typeof artifact !== "object") {
    return null;
  }
  const kind = normalizeRunKind(artifact.kind, fallback.kind || "other");
  const artifactType = String(artifact.artifact_type || artifact.artifactType || fallback.artifactType || "artifact").trim() || "artifact";
  const source = String(artifact.source || fallback.source || "").trim();
  const sourceId = artifact.source_id ?? artifact.sourceId ?? fallback.sourceId ?? "";
  const createdAt = normalizeEventTimestamp(artifact.created_at ?? artifact.createdAt ?? fallback.createdAt);
  const toolCallId = String(artifact.tool_call_id || artifact.toolCallId || fallback.toolCallId || "").trim();
  const toolName = String(artifact.tool_name || artifact.toolName || "").trim();
  const iteration = artifact.iteration ?? fallback.iteration ?? "";
  const inferredToolId = toolCallId
    ? `tool:${toolCallId}`
    : toolName && iteration !== "" && iteration !== null && iteration !== undefined
      ? `tool:${toolName}:${iteration}`
      : "";
  const artifactId = String(artifact.artifact_id || artifact.artifactId || inferredToolId || `${source || artifactType}:${sourceId || createdAt}`).trim();
  const snapshots = artifact.snapshots_available || artifact.snapshotsAvailable || {};
  return {
    artifactId,
    artifactType,
    kind,
    status: String(artifact.status || artifact.state || fallback.status || "completed").trim() || "completed",
    phase: String(artifact.phase || fallback.phase || "").trim(),
    title: String(artifact.title || artifact.tool_name || artifact.toolName || artifact.path || artifactType).trim(),
    detail: String(artifact.detail || artifact.diff_preview || artifact.diffPreview || "").trim(),
    source,
    sourceId: sourceId === null || sourceId === undefined ? "" : String(sourceId),
    createdAt,
    toolName,
    toolCallId,
    iteration,
    path: String(artifact.path || "").trim(),
    action: String(artifact.action || "").trim(),
    diffLen: coerceNonNegativeInteger(artifact.diff_len ?? artifact.diffLen),
    diffPreview: String(artifact.diff_preview || artifact.diffPreview || ""),
    snapshotsAvailable: {
      before: coerceBoolean(snapshots.before),
      after: coerceBoolean(snapshots.after),
    },
    metadata: artifact.metadata && typeof artifact.metadata === "object" ? artifact.metadata : {},
  };
}

function normalizeDiffSummary(payload) {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const actions = payload.actions && typeof payload.actions === "object" ? payload.actions : {};
  const paths = Array.isArray(payload.paths)
    ? payload.paths.map((path) => String(path || "").trim()).filter(Boolean)
    : [];
  return {
    schemaVersion: coerceNonNegativeInteger(payload.schema_version ?? payload.schemaVersion),
    changedFiles: coerceNonNegativeInteger(payload.changed_files ?? payload.changedFiles ?? paths.length),
    changeCount: coerceNonNegativeInteger(payload.change_count ?? payload.changeCount),
    additions: coerceNonNegativeInteger(payload.additions),
    deletions: coerceNonNegativeInteger(payload.deletions),
    paths,
    actions: Object.fromEntries(
      Object.entries(actions)
        .map(([action, count]) => [String(action || "unknown").trim() || "unknown", coerceNonNegativeInteger(count)])
        .filter(([action, count]) => action && count > 0),
    ),
  };
}

function normalizeWorktreeSandbox(payload) {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const metadata = payload.metadata && typeof payload.metadata === "object" ? payload.metadata : payload;
  const sandboxPath = String(metadata.sandbox_path || metadata.sandboxPath || "").trim();
  if (!sandboxPath) {
    return null;
  }
  return {
    sandboxPath,
    status: String(metadata.status || payload.status || "").trim(),
    reason: String(metadata.reason || "").trim(),
    cleanupSupported: coerceBoolean(metadata.cleanup_supported ?? metadata.cleanupSupported),
    repositoryRoot: String(metadata.repository_root || metadata.repositoryRoot || "").trim(),
    baseBranch: String(metadata.base_branch || metadata.baseBranch || "").trim(),
    baseCommit: String(metadata.base_commit || metadata.baseCommit || "").trim(),
    cleanupPending: false,
    cleanupResult: null,
  };
}

function findWorktreeSandbox(parts = [], artifacts = []) {
  for (const part of parts) {
    if (part?.partType === "worktree_sandbox") {
      return normalizeWorktreeSandbox(part.metadata);
    }
  }
  for (const artifact of artifacts) {
    if (artifact?.artifactType === "worktree_sandbox" || artifact?.kind === "work") {
      return normalizeWorktreeSandbox(artifact);
    }
  }
  return null;
}

function normalizeWorkState(payload) {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const objective = String(payload.objective || "").trim();
  if (!objective) {
    return null;
  }
  return {
    sessionId: String(payload.session_id || payload.sessionId || "").trim() || null,
    objective,
    kind: String(payload.kind || "task").trim() || "task",
    status: String(payload.status || "active").trim() || "active",
    steps: coerceStringList(payload.steps),
    constraints: coerceStringList(payload.constraints),
    doneCriteria: coerceStringList(payload.done_criteria || payload.doneCriteria),
    longRunning: coerceBoolean(payload.long_running ?? payload.longRunning),
    codingTask: coerceBoolean(payload.coding_task ?? payload.codingTask),
    expectsCodeChange: coerceBoolean(payload.expects_code_change ?? payload.expectsCodeChange),
    expectsVerification: coerceBoolean(payload.expects_verification ?? payload.expectsVerification),
    currentStep: String(payload.current_step || payload.currentStep || "not set").trim() || "not set",
    nextStep: String(payload.next_step || payload.nextStep || "not set").trim() || "not set",
    completedSteps: coerceStringList(payload.completed_steps || payload.completedSteps),
    pendingSteps: coerceStringList(payload.pending_steps || payload.pendingSteps),
    blockers: coerceStringList(payload.blockers),
    verificationTargets: coerceStringList(payload.verification_targets || payload.verificationTargets),
    resumeHint: String(payload.resume_hint || payload.resumeHint || "").trim(),
    lastProgressSignals: coerceStringList(payload.last_progress_signals || payload.lastProgressSignals),
    fileChangeCount: coerceNonNegativeInteger(payload.file_change_count ?? payload.fileChangeCount),
    touchedPaths: coerceStringList(payload.touched_paths || payload.touchedPaths),
    verificationAttempted: coerceBoolean(payload.verification_attempted ?? payload.verificationAttempted),
    verificationPassed: coerceBoolean(payload.verification_passed ?? payload.verificationPassed),
    lastNextAction: String(payload.last_next_action || payload.lastNextAction || "").trim(),
    activeDelegateTaskId: String(payload.active_delegate_task_id || payload.activeDelegateTaskId || "").trim() || null,
    activeDelegatePromptType: String(payload.active_delegate_prompt_type || payload.activeDelegatePromptType || "").trim() || null,
    updatedAt: normalizeEventTimestamp(payload.updated_at ?? payload.updatedAt),
  };
}

function normalizeEventTimestamp(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue) || numericValue <= 0) {
    return Date.now();
  }
  return numericValue > 1_000_000_000_000 ? numericValue : numericValue * 1000;
}

function coerceEventPayload(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function shortRunId(runId) {
  const normalized = String(runId || "run").replace(/^run[_-]?/, "");
  return normalized.length > 8 ? normalized.slice(0, 8) : normalized;
}

function runStatusLabel(status, copy) {
  return copy.run.statusLabels[status] || copy.run.statusLabels.running;
}

function sessionStatusLabel(session, copy) {
  const status = String(session?.status?.status || "idle").trim() || "idle";
  return copy.run.statusLabels[status] || status;
}

function runTone(status, fallbackTone = "running") {
  if (status === "completed") {
    return fallbackTone === "warning" ? "warning" : "success";
  }
  if (status === "failed") {
    return "error";
  }
  if (status === "cancelled") {
    return "warning";
  }
  return fallbackTone || "running";
}

function buildHttpApiUrl(wsUrl, pathname) {
  const url = new URL(wsUrl);
  url.protocol = url.protocol === "wss:" ? "https:" : "http:";
  url.pathname = pathname;
  url.search = "";
  return url;
}

function buildRunCancelUrl(wsUrl, runId, sessionId) {
  const url = buildHttpApiUrl(wsUrl, `/api/runs/${encodeURIComponent(runId)}/cancel`);
  url.searchParams.set("session_id", sessionId);
  return url.toString();
}

function isTerminalRunStatus(status) {
  return TERMINAL_RUN_STATUSES.has(status);
}

function getActiveRun(session) {
  if (!session?.runs?.length) {
    return null;
  }
  return session.runs.find((run) => run.runId === session.activeRunId) || session.runs[0];
}

function shouldLoadRunSummary({ showRunSummary }, run) {
  return Boolean(
    showRunSummary
    && run
    && isTerminalRunStatus(run.status)
    && !run.summary
    && !run.summaryLoading
    && !run.summaryError
    && coerceNonNegativeInteger(run.summaryNotFoundAttempts) < RUN_SUMMARY_NOT_FOUND_RETRY_LIMIT,
  );
}

function shouldLoadRunTrace(run) {
  if (!run || run.traceLoading) {
    return false;
  }
  const hasNeededFileChanges = (run.fileChanges || []).length > 0 || !(run.summary?.fileChanges || []).length;
  return !(run.traceLoaded && hasNeededFileChanges);
}

function createRunViewState({ runId, sessionId, status = "running", createdAt, updatedAt = createdAt, finishedAt = null }) {
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

function buildRunSummaryPath(runId, sessionId) {
  return `/api/runs/${encodeURIComponent(runId)}/summary?session_id=${encodeURIComponent(sessionId)}`;
}

function buildRunTracePath(runId, sessionId) {
  return `/api/runs/${encodeURIComponent(runId)}?session_id=${encodeURIComponent(sessionId)}`;
}

function buildRunFileChangeRevertPath(runId, sessionId, changeId) {
  return `/api/runs/${encodeURIComponent(runId)}/file-changes/${encodeURIComponent(changeId)}/revert?session_id=${encodeURIComponent(sessionId)}`;
}

function buildWorktreeCleanupPath() {
  return "/api/worktrees/cleanup";
}

function buildRunsPath(sessionId) {
  return `/api/runs?session_id=${encodeURIComponent(sessionId)}&limit=${RUN_HISTORY_LIMIT}`;
}

function statusFromRunEvent(eventType, payload, eventStatus = "") {
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

function formatRunFinishDetail(payload, copy) {
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

function normalizeRunSummary(payload) {
  if (!payload || typeof payload !== "object") {
    return null;
  }

  const verification = payload.verification && typeof payload.verification === "object" ? payload.verification : {};
  const counts = payload.counts && typeof payload.counts === "object" ? payload.counts : {};
  const artifactCounts = payload.artifact_counts && typeof payload.artifact_counts === "object" ? payload.artifact_counts : {};
  return {
    schemaVersion: coerceNonNegativeInteger(payload.schema_version ?? payload.schemaVersion),
    runId: String(payload.run_id || payload.runId || "").trim(),
    sessionId: String(payload.session_id || payload.sessionId || "").trim(),
    status: String(payload.status || "completed").trim() || "completed",
    objective: String(payload.objective || "").trim(),
    durationSeconds: Number.isFinite(Number(payload.duration_seconds ?? payload.durationSeconds))
      ? Number(payload.duration_seconds ?? payload.durationSeconds)
      : null,
    tools: Array.isArray(payload.tools)
      ? payload.tools
          .map((tool) => ({
            name: String(tool?.name || "").trim(),
            count: coerceNonNegativeInteger(tool?.count),
          }))
          .filter((tool) => tool.name)
      : [],
    fileChanges: Array.isArray(payload.file_changes || payload.fileChanges)
      ? (payload.file_changes || payload.fileChanges)
          .map((change) => ({
            changeId: String(change?.change_id || change?.changeId || "").trim(),
            path: String(change?.path || "").trim(),
            action: String(change?.action || "").trim(),
            toolName: String(change?.tool_name || change?.toolName || "").trim(),
            diffLen: coerceNonNegativeInteger(change?.diff_len ?? change?.diffLen),
            diff: String(change?.diff || ""),
            snapshotsAvailable: {
              before: coerceBoolean(change?.snapshots_available?.before ?? change?.snapshotsAvailable?.before),
              after: coerceBoolean(change?.snapshots_available?.after ?? change?.snapshotsAvailable?.after),
            },
          }))
          .filter((change) => change.path)
      : [],
    diffSummary: normalizeDiffSummary(payload.diff_summary || payload.diffSummary),
    verification: {
      attempted: coerceBoolean(verification.attempted),
      passed: coerceBoolean(verification.passed),
      status: String(verification.status || "not_attempted").trim() || "not_attempted",
      name: String(verification.name || "").trim(),
      summary: String(verification.summary || "").trim(),
    },
    completion: payload.completion && typeof payload.completion === "object" ? payload.completion : {},
    nextAction: String(payload.next_action || payload.nextAction || "").trim(),
    warnings: coerceStringList(payload.warnings),
    artifactCounts: {
      total: coerceNonNegativeInteger(artifactCounts.total),
      tool: coerceNonNegativeInteger(artifactCounts.tool),
      file: coerceNonNegativeInteger(artifactCounts.file),
      verification: coerceNonNegativeInteger(artifactCounts.verification),
    },
    counts: {
      events: coerceNonNegativeInteger(counts.events),
      parts: coerceNonNegativeInteger(counts.parts),
      toolCalls: coerceNonNegativeInteger(counts.tool_calls ?? counts.toolCalls),
      fileChanges: coerceNonNegativeInteger(counts.file_changes ?? counts.fileChanges),
    },
  };
}

function describeRunEvent(eventType, payload, copy) {
  if (!TIMELINE_EVENT_TYPES.has(eventType)) {
    return null;
  }

  if (eventType === "run_started") {
    return { label: copy.run.runStarted, detail: copy.run.preparingTask, tone: "running" };
  }

  if (eventType === "llm_status") {
    const message = String(payload.message || copy.run.thinking);
    return {
      label: message === "processing" ? copy.run.thinking : copy.run.llmStatus,
      detail: message === "processing" ? copy.run.preparingPrompt : message,
      tone: "running",
    };
  }

  if (eventType === "tool_started") {
    if (payload.tool_name === "verify") {
      return null;
    }
    return {
      label: `${copy.run.tool}: ${payload.tool_name || copy.run.unknownTool}`,
      detail: payload.args_preview || copy.run.executingTool,
      tone: "running",
    };
  }

  if (eventType === "verification_started") {
    return {
      label: `${copy.run.verifying}: ${payload.action || copy.run.auto}`,
      detail: payload.path ? `${copy.run.pathPrefix} ${payload.path}` : copy.run.runningChecks,
      tone: "running",
    };
  }

  if (eventType === "verification_result") {
    const ok = payload.ok !== false;
    return {
      label: ok ? copy.run.verificationPassed : copy.run.verificationFailed,
      detail: payload.result_preview || copy.run.verificationCompleted,
      tone: ok ? "success" : "error",
    };
  }

  if (eventType === "file_changed") {
    return {
      label: `${copy.run.fileChanged || "File changed"}: ${payload.path || "?"}`,
      detail: payload.diff_preview || payload.action || "",
      tone: "running",
    };
  }

  if (eventType === "tool_input_delta") {
    return {
      label: `${copy.trace.filters.tool}: ${payload.tool_name || copy.run.unknownTool}`,
      detail: payload.input_delta || "",
      tone: "running",
    };
  }

  if (eventType === "reasoning_delta") {
    return {
      label: copy.trace.filters.llm,
      detail: payload.content_delta || "",
      tone: "running",
    };
  }

  if (eventType === "permission_requested") {
    return {
      label: `${copy.trace.filters.permission}: ${payload.tool_name || copy.run.unknownTool}`,
      detail: payload.reason || payload.args_preview || "",
      tone: "warning",
    };
  }

  if (eventType === "permission_granted" || eventType === "permission_denied") {
    const granted = eventType === "permission_granted";
    return {
      label: `${copy.trace.filters.permission}: ${payload.tool_name || copy.run.unknownTool}`,
      detail: payload.resolution_reason || payload.status || "",
      tone: granted ? "success" : "error",
    };
  }

  if (eventType === "run_finished") {
    return {
      label: payload.had_tool_error ? copy.run.completedWithWarnings : copy.run.completed,
      detail: formatRunFinishDetail(payload, copy) || copy.run.finalDelivered,
      tone: payload.had_tool_error ? "warning" : "success",
    };
  }

  if (eventType === "run_failed") {
    const cancelled = payload.status === "cancelled";
    return {
      label: cancelled ? copy.run.cancelled : copy.run.failed,
      detail: payload.error || copy.run.stopped,
      tone: cancelled ? "warning" : "error",
    };
  }

  if (eventType === "run_cancelled") {
    return {
      label: copy.run.cancelled,
      detail: payload.error || copy.run.stopped,
      tone: "warning",
    };
  }

  if (eventType === "run_cancel_requested") {
    return {
      label: copy.trace.cancelling,
      detail: payload.status || "",
      tone: "warning",
    };
  }

  return null;
}

export function formatEventTime(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return "--:--";
  }
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

export function useChatClient() {
  const storedExternalChatId = readStoredValue(STORAGE_KEYS.activeExternalChatId, "");
  const initialLanguage = readStoredChoice(STORAGE_KEYS.language, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES);
  const initialColorScheme = readStoredChoice(STORAGE_KEYS.colorScheme, DEFAULT_COLOR_SCHEME, SUPPORTED_COLOR_SCHEMES);
  const initialCopy = getDisplayCopy(initialLanguage);
  const initialSession = createSession(
    isExternalChannelSessionId(storedExternalChatId) ? generateExternalChatId() : storedExternalChatId || generateExternalChatId(),
  );

  const state = reactive({
    wsUrl: readStoredValue(STORAGE_KEYS.wsUrl, DEFAULT_WS_URL),
    displayName: readStoredValue(STORAGE_KEYS.displayName, "Local browser"),
    showRunTimeline: readStoredBoolean(STORAGE_KEYS.showRunTimeline, true),
    showRunSummary: readStoredBoolean(STORAGE_KEYS.showRunSummary, true),
    showRunTrace: readStoredBoolean(STORAGE_KEYS.showRunTrace, true),
    language: initialLanguage,
    colorScheme: initialColorScheme,
    activeExternalChatId: initialSession.externalChatId,
    sessions: [initialSession],
    connectionState: "disconnected",
    notice: {
      text: initialCopy.notices.connectingGateway,
      tone: "info",
    },
  });

  const copy = computed(() => getDisplayCopy(state.language));
  const prompts = computed(() => copy.value.prompts);

  const messageText = ref("");
  const messageInput = ref(null);
  const messageStage = ref(null);
  const sidebarOpen = ref(false);
  const sidebarCollapsed = ref(readStoredBoolean(STORAGE_KEYS.sidebarCollapsed, false));
  const sessionChannelFilter = ref("all");
  const settingsOpen = ref(false);
  const settingsSection = ref("general");
  const settingsForm = reactive({
    wsUrl: state.wsUrl,
    displayName: state.displayName,
    externalChatId: state.activeExternalChatId,
    showRunTimeline: state.showRunTimeline,
    showRunSummary: state.showRunSummary,
    showRunTrace: state.showRunTrace,
    language: state.language,
    colorScheme: state.colorScheme,
  });
  const settingsState = reactive({
    channelsLoading: false,
    channelsError: "",
    channelsNotice: "",
    channels: {
      connected: [],
      available: [],
      channels: [],
    },
    channelConnectForm: {
      type: "",
      name: "",
      token: "",
    },
    providersLoading: false,
    providersError: "",
    providersNotice: "",
    providers: {
      default_provider: null,
      connected: [],
      available: [],
    },
    connectForm: {
      providerId: "",
      apiKey: "",
      baseUrl: "",
      showAdvanced: false,
    },
    modelsLoading: false,
    modelsError: "",
    modelsNotice: "",
    models: {
      default_provider: null,
      active_model: "",
      providers: [],
    },
    modelSelections: {},
    customModels: {},
    scheduleLoading: false,
    scheduleError: "",
    scheduleNotice: "",
    schedule: {
      default_timezone: "UTC",
      common_timezones: [],
    },
    scheduleForm: {
      defaultTimezone: "UTC",
    },
    cronJobsLoading: false,
    cronJobsError: "",
    cronJobsNotice: "",
    cronJobs: [],
    cronJobForm: {
      showEditor: false,
      sessionId: "",
      jobId: "",
      mode: "cron",
      name: "",
      message: "",
      everySeconds: "3600",
      cronExpr: "0 9 * * *",
      at: "",
      timezone: "UTC",
      deliver: true,
    },
    mcpLoading: false,
    mcpError: "",
    mcpNotice: "",
    mcpToolGroupsExpanded: {},
    mcp: {
      servers: [],
      runtime: {
        connected: false,
        connecting: false,
        connect_failures: 0,
        tool_names: [],
      },
    },
    mcpForm: {
      showEditor: false,
      editingId: "",
      serverId: "",
      type: "stdio",
      command: "",
      argsText: "",
      url: "",
      envJson: "",
      headersJson: "",
      toolTimeout: "30",
      enabledToolsText: "*",
      showAdvanced: false,
      showJsonInput: false,
      jsonText: "",
    },
  });
  const permissionState = reactive({
    loading: false,
    error: "",
    requests: [],
    resolvingIds: {},
  });

  let activeSocket = null;
  let colorSchemeMediaQuery = null;
  let clientDisposed = false;
  const runSummaryTimers = new Map();
  const runBackfillTimes = new Map();

  function applyDocumentPreferences() {
    if (typeof document === "undefined") {
      return;
    }
    document.documentElement.lang = LANGUAGE_ATTRIBUTES[state.language] || LANGUAGE_ATTRIBUTES[DEFAULT_LANGUAGE];
    document.documentElement.dataset.colorScheme = getResolvedColorScheme(state.colorScheme);
    document.documentElement.dataset.colorSchemePreference = state.colorScheme;
  }

  function handleSystemColorSchemeChange() {
    if (state.colorScheme === "system") {
      applyDocumentPreferences();
    }
  }

  function addColorSchemeListener() {
    if (typeof window === "undefined" || !window.matchMedia) {
      return;
    }
    colorSchemeMediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    if (colorSchemeMediaQuery.addEventListener) {
      colorSchemeMediaQuery.addEventListener("change", handleSystemColorSchemeChange);
      return;
    }
    colorSchemeMediaQuery.addListener?.(handleSystemColorSchemeChange);
  }

  function removeColorSchemeListener() {
    if (!colorSchemeMediaQuery) {
      return;
    }
    if (colorSchemeMediaQuery.removeEventListener) {
      colorSchemeMediaQuery.removeEventListener("change", handleSystemColorSchemeChange);
    } else {
      colorSchemeMediaQuery.removeListener?.(handleSystemColorSchemeChange);
    }
    colorSchemeMediaQuery = null;
  }

  const currentSession = computed(() => {
    return state.sessions.find((session) => session.externalChatId === state.activeExternalChatId) || null;
  });

  const sidebarSessions = computed(() => {
    if (sessionChannelFilter.value === "web") {
      return state.sessions.filter((session) => !session.channel || session.channel === "web");
    }
    return state.sessions;
  });

  const currentWorkState = computed(() => currentSession.value?.workState || null);

  const currentMessages = computed(() => currentSession.value?.messages || []);

  const currentEntries = computed(() => currentSession.value?.entries || []);

  const currentRuns = computed(() => currentSession.value?.runs || []);

  const currentRunsLoading = computed(() => Boolean(currentSession.value?.runsLoading));

  const currentRunsError = computed(() => currentSession.value?.runsError || "");

  const currentRun = computed(() => {
    return getActiveRun(currentSession.value);
  });

  const currentRunTimeline = computed(() => {
    const events = currentRun.value?.events || [];
    return events.slice(-MAX_TIMELINE_EVENTS);
  });

  const currentRunSummary = computed(() => {
    const run = currentRun.value;
    const latestEvent = currentRunTimeline.value.at(-1);
    if (!run || !latestEvent) {
      return null;
    }
    return {
      shortId: shortRunId(run.runId),
      statusLabel: runStatusLabel(run.status, copy.value),
      title: latestEvent.label,
      tone: runTone(run.status, latestEvent.tone),
    };
  });

  const currentPermissionRequests = computed(() => {
    const session = currentSession.value;
    if (!session) {
      return permissionState.requests;
    }
    const sessionIds = new Set([
      session.sessionId,
      session.externalChatId,
      session.transportExternalChatId,
    ].filter(Boolean));
    return permissionState.requests.filter((request) => {
      if (request.status && request.status !== "pending") {
        return false;
      }
      return !request.sessionId || sessionIds.has(request.sessionId) || sessionIds.has(request.externalChatId);
    });
  });

  const settingsTitle = computed(() => copy.value.settingsTitles[settingsSection.value] || copy.value.settingsTitles.general);

  const sessionMeta = computed(() => {
    const session = currentSession.value;
    return `${getSessionTitle(session)} · ${getSessionDisplayId(session)} · ${sessionStatusLabel(session, copy.value)}`;
  });

  const runtimeHint = computed(() => currentSession.value?.externalChatId || copy.value.session.noActiveChat);

  const composerHint = computed(() => {
    const session = currentSession.value;
    if (session?.channel && session.channel !== "web") {
      return copy.value.composer.readOnlyChannel(session.channel);
    }
    return runtimeHint.value;
  });

  const connectionLabel = computed(() => {
    const labels = copy.value.connection;
    return labels[state.connectionState] || labels.disconnected;
  });

  const connectButtonLabel = computed(() => {
    const labels = {
      disconnected: copy.value.connection.retry,
      connecting: copy.value.connection.connecting,
      connected: copy.value.connection.reconnect,
    };
    return labels[state.connectionState] || labels.disconnected;
  });

  const statusDotClass = computed(() => ({
    "status-dot--connected": state.connectionState === "connected",
    "status-dot--connecting": state.connectionState === "connecting",
  }));

  const currentSessionReadOnly = computed(() => {
    const session = currentSession.value;
    return Boolean(session && session.channel !== "web");
  });

  const sendDisabled = computed(() => state.connectionState !== "connected" || currentSessionReadOnly.value);

  function setMessageInputRef(element) {
    messageInput.value = element;
  }

  function setMessageStageRef(element) {
    messageStage.value = element;
  }

  function setMessageText(value) {
    messageText.value = value;
  }

  function saveRunPanelVisibilitySettings(showRunTimeline, showRunSummary, showRunTrace) {
    state.showRunTimeline = Boolean(showRunTimeline);
    state.showRunSummary = Boolean(showRunSummary);
    state.showRunTrace = Boolean(showRunTrace);
    writeStoredValue(STORAGE_KEYS.showRunTimeline, String(state.showRunTimeline));
    writeStoredValue(STORAGE_KEYS.showRunSummary, String(state.showRunSummary));
    writeStoredValue(STORAGE_KEYS.showRunTrace, String(state.showRunTrace));
    if (state.showRunSummary) {
      maybeLoadRunSummaryForSession(currentSession.value);
    } else {
      clearAllRunSummaryTimers();
      for (const session of state.sessions) {
        for (const run of session.runs || []) {
          run.summaryLoading = false;
        }
      }
    }
  }

  function clearAllRunSummaryTimers() {
    for (const timer of runSummaryTimers.values()) {
      clearTimeout(timer);
    }
    runSummaryTimers.clear();
  }

  function saveDisplaySettings(language, colorScheme) {
    state.language = normalizeChoice(language, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES);
    state.colorScheme = normalizeChoice(colorScheme, DEFAULT_COLOR_SCHEME, SUPPORTED_COLOR_SCHEMES);
    writeStoredValue(STORAGE_KEYS.language, state.language);
    writeStoredValue(STORAGE_KEYS.colorScheme, state.colorScheme);
    applyDocumentPreferences();
  }

  function rebuildLocalizedRunEvents() {
    for (const session of state.sessions) {
      for (const run of session.runs || []) {
        run.events = (run.rawEvents || [])
          .map((event) => {
            const description = describeRunEvent(event.eventType, event.payload, copy.value);
            return description
            ? {
                id: `${event.id}-localized`,
                eventType: event.eventType,
                kind: event.kind,
                status: event.status,
                createdAt: event.createdAt,
                payload: event.payload,
                artifact: event.artifact,
                ...description,
              }
              : null;
          })
          .filter(Boolean)
          .slice(-MAX_RUN_EVENTS);
      }
    }
  }

  watch(settingsOpen, (isOpen) => {
    document.body.classList.toggle("settings-open", isOpen);
  });

  watch(
    () => state.activeExternalChatId,
    () => {
      if (settingsOpen.value && settingsSection.value === "schedule") {
        loadCronJobs();
      }
    },
  );

  watch(
    () => [currentSession.value?.externalChatId, currentSession.value?.sessionId],
    () => {
      void loadCurrentSessionRuns();
    },
    { immediate: true },
  );

  watch(
    () => [currentSession.value?.externalChatId, currentRun.value?.runId, currentRun.value?.status],
    () => {
      maybeLoadRunSummaryForSession(currentSession.value);
      maybeLoadRunTraceForSession(currentSession.value);
    },
    { immediate: true },
  );

  watch(sidebarOpen, (isOpen) => {
    document.body.classList.toggle("sidebar-open", isOpen);
  });

  watch(
    () => [settingsForm.showRunTimeline, settingsForm.showRunSummary, settingsForm.showRunTrace],
    ([showRunTimeline, showRunSummary, showRunTrace]) => {
      saveRunPanelVisibilitySettings(showRunTimeline, showRunSummary, showRunTrace);
    },
  );

  watch(
    () => [settingsForm.language, settingsForm.colorScheme],
    ([language, colorScheme]) => {
      saveDisplaySettings(language, colorScheme);
    },
  );

  watch(
    () => [state.language, state.colorScheme],
    ([language], [previousLanguage] = []) => {
      applyDocumentPreferences();
      if (previousLanguage && language !== previousLanguage) {
        rebuildLocalizedRunEvents();
      }
    },
    { immediate: true },
  );

  function sortSessions() {
    state.sessions.sort((left, right) => right.updatedAt - left.updatedAt);
  }

  function getSessionDisplayId(session) {
    if (!session) {
      return copy.value.session.noActiveChat;
    }
    if (session.channel && session.channel !== "web") {
      return session.sessionId || `${session.channel}:${session.transportExternalChatId || session.externalChatId}`;
    }
    return session.sessionId || session.externalChatId;
  }

  function getSessionApiId(session) {
    return session?.sessionId || "";
  }

  function getSessionTitle(session) {
    if (!session || session.title === "New chat") {
      return copy.value.session.newChat;
    }
    return session.title;
  }

  function ensureSession(externalChatId, sessionId) {
    const resolvedExternalChatId = externalChatId || generateExternalChatId();
    let session = state.sessions.find((entry) => entry.externalChatId === resolvedExternalChatId);
    if (!session) {
      session = createSession(resolvedExternalChatId);
      session.messages = [
        makeMessage(
          "assistant",
          copy.value.session.liveGatewayThread,
          "OpenSprite",
        ),
      ];
      state.sessions.unshift(session);
    }
    if (sessionId) {
      session.sessionId = sessionId;
      session.channel = channelFromSessionId(sessionId);
    }
    session.transportExternalChatId = resolvedExternalChatId;
    session.updatedAt = Date.now();
    return session;
  }

  function applySessionStatus(payload) {
    const sessionId = String(payload?.session_id || payload?.sessionId || "").trim();
    if (!sessionId) {
      return;
    }
    const channel = String(payload?.channel || channelFromSessionId(sessionId) || "web").trim() || "web";
    const transportExternalChatId = String(payload?.external_chat_id || payload?.externalChatId || "").trim()
      || externalChatIdFromSessionId(sessionId)
      || generateExternalChatId();
    const externalChatId = channel === "web" ? transportExternalChatId : sessionId;
    const session = ensureSession(externalChatId, sessionId);
    session.channel = channel;
    session.transportExternalChatId = transportExternalChatId;
    session.status = {
      status: String(payload?.status || "idle").trim() || "idle",
      updatedAt: normalizeEventTimestamp(payload?.updated_at ?? payload?.updatedAt),
      metadata: payload?.metadata && typeof payload.metadata === "object" ? payload.metadata : {},
    };
    if (session.status.status !== "idle") {
      session.updatedAt = session.status.updatedAt;
      sortSessions();
    }
  }

  function addMessage(externalChatId, message) {
    const session = ensureSession(externalChatId);
    session.messages.push(message);
    session.updatedAt = message.createdAt;
    if (message.role === "user" && session.title === "New chat") {
      session.title = summarizeTitle(message.text);
    }
    sortSessions();
  }

  function findOrCreateRun(session, runId, createdAt) {
    let run = session.runs.find((entry) => entry.runId === runId);
    if (!run) {
      run = createRunViewState({
        runId,
        sessionId: session.sessionId,
        createdAt,
      });
      session.runs.unshift(run);
    }
    run.sessionId = run.sessionId || session.sessionId;
    session.activeRunId = runId;
    return run;
  }

  function mergeSessionWorkState(session, updates) {
    if (!session || !updates) {
      return;
    }
    const normalized = normalizeWorkState({
      ...(session.workState || {}),
      ...updates,
    });
    if (normalized) {
      session.workState = normalized;
    }
  }

  function applyWorkPlanEvent(session, payload, createdAt) {
    const steps = coerceStringList(payload.steps);
    mergeSessionWorkState(session, {
      objective: payload.objective,
      kind: payload.kind,
      status: "active",
      steps,
      constraints: payload.constraints,
      doneCriteria: payload.done_criteria,
      longRunning: payload.long_running,
      codingTask: payload.coding_task,
      expectsCodeChange: payload.expects_code_change,
      expectsVerification: payload.expects_verification,
      currentStep: steps[0] || "not set",
      nextStep: steps[1] || "not set",
      pendingSteps: steps,
      updatedAt: createdAt,
    });
  }

  function applyWorkProgressEvent(session, payload, createdAt) {
    if (!session?.workState) {
      return;
    }
    const progress = payload?.work_progress && typeof payload.work_progress === "object" ? payload.work_progress : payload;
    const touchedPaths = [
      ...session.workState.touchedPaths,
      ...coerceStringList(progress.touched_paths),
    ];
    mergeSessionWorkState(session, {
      status: progress.status || session.workState.status,
      fileChangeCount: session.workState.fileChangeCount + coerceNonNegativeInteger(progress.file_change_count),
      touchedPaths: [...new Set(touchedPaths)],
      verificationAttempted: session.workState.verificationAttempted || coerceBoolean(progress.verification_attempted),
      verificationPassed: session.workState.verificationPassed || coerceBoolean(progress.verification_passed),
      lastNextAction: progress.next_action || session.workState.lastNextAction,
      lastProgressSignals: progress.progress_signals || session.workState.lastProgressSignals,
      updatedAt: createdAt,
    });
  }

  function applyWorkStateFromRunEvent(session, eventType, payload, createdAt) {
    if (eventType === "work_plan.created") {
      applyWorkPlanEvent(session, payload, createdAt);
      return;
    }
    if (eventType === "work_progress.updated") {
      applyWorkProgressEvent(session, payload, createdAt);
    }
  }

  function upsertRunArtifact(run, artifact) {
    const normalized = normalizeRunArtifact(artifact);
    if (!normalized) {
      return null;
    }
    const artifacts = run.artifacts || [];
    const existingIndex = artifacts.findIndex((entry) => entry.artifactId === normalized.artifactId);
    if (existingIndex >= 0) {
      artifacts[existingIndex] = { ...artifacts[existingIndex], ...normalized };
    } else {
      artifacts.push(normalized);
    }
    artifacts.sort((left, right) => Number(left.createdAt || 0) - Number(right.createdAt || 0));
    if (artifacts.length > MAX_RUN_ARTIFACTS) {
      artifacts.splice(0, artifacts.length - MAX_RUN_ARTIFACTS);
    }
    run.artifacts = artifacts;
    return normalized;
  }

  function applyToolArtifactToParts(run, artifact) {
    if (!artifact || artifact.kind !== "tool" || !artifact.toolCallId || !TERMINAL_PART_STATES.has(artifact.status)) {
      return;
    }
    run.parts = (run.parts || []).map((part) => {
      const metadata = part.metadata && typeof part.metadata === "object" ? part.metadata : {};
      const toolCallId = String(metadata.tool_call_id || metadata.toolCallId || part.artifact?.toolCallId || "").trim();
      if (part.partType !== "tool_call" || toolCallId !== artifact.toolCallId) {
        return part;
      }
      const finishedAt = artifact.metadata?.finished_at || metadata.finished_at;
      const nextMetadata = { ...metadata, state: artifact.status };
      if (finishedAt) {
        nextMetadata.finished_at = finishedAt;
      }
      return {
        ...part,
        state: artifact.status,
        metadata: nextMetadata,
        artifact: part.artifact ? { ...part.artifact, status: artifact.status } : part.artifact,
      };
    });
  }

  function applyRunEventArtifact(run, artifact) {
    const normalized = upsertRunArtifact(run, artifact);
    applyToolArtifactToParts(run, normalized);
    if (!normalized || normalized.kind !== "file" || !normalized.path) {
      return;
    }
    const existingIndex = run.fileChanges.findIndex((change) => {
      if (normalized.sourceId && change.changeId) {
        return String(change.changeId) === String(normalized.sourceId);
      }
      return change.path === normalized.path && (!normalized.action || change.action === normalized.action);
    });
    const preview = {
      changeId: normalized.sourceId || normalized.artifactId,
      path: normalized.path,
      action: normalized.action,
      toolName: normalized.toolName,
      diffLen: normalized.diffLen,
      diff: "",
      diffPreview: normalized.diffPreview,
      beforeContent: null,
      afterContent: null,
      snapshotsAvailable: normalized.snapshotsAvailable,
      artifact: normalized,
      createdAt: normalized.createdAt,
    };
    if (existingIndex >= 0) {
      run.fileChanges[existingIndex] = { ...run.fileChanges[existingIndex], ...preview };
      return;
    }
    run.fileChanges.push(preview);
  }

  function handleRunEvent(payload) {
    const externalChatId = payload.external_chat_id || currentSession.value?.externalChatId || generateExternalChatId();
    const session = ensureSession(externalChatId, payload.session_id);
    const runId = String(payload.run_id || `run-${Date.now().toString(36)}-${randomToken()}`);
    const eventType = String(payload.event_type || "run_event");
    const eventPayload = coerceEventPayload(payload.payload);
    const eventKind = normalizeRunKind(payload.kind, inferRunEventKind(eventType));
    const eventStatus = String(payload.status || inferRunEventStatus(eventType, eventPayload)).trim();
    const createdAt = normalizeEventTimestamp(payload.created_at);
    const eventArtifact = normalizeRunArtifact(payload.artifact, {
      kind: eventKind,
      status: eventStatus,
      source: "event",
      sourceId: `${eventType}-${createdAt}`,
      createdAt,
    });
    const run = findOrCreateRun(session, runId, createdAt);
    applyWorkStateFromRunEvent(session, eventType, eventPayload, createdAt);
    const nextStatus = statusFromRunEvent(eventType, eventPayload, eventStatus);
    const rawEvent = {
      id: `${runId}-raw-${eventType}-${createdAt}-${randomToken()}`,
      eventType,
      kind: eventKind,
      status: eventStatus || "completed",
      createdAt,
      payload: eventPayload,
      artifact: eventArtifact,
    };
    run.rawEvents.push(rawEvent);
    run.rawEvents = compactRunEvents(run.rawEvents);
    updateLiveTraceEventCounts(run, rawEvent);

    if (nextStatus) {
      run.status = nextStatus;
    } else if (!["completed", "failed", "cancelled"].includes(run.status)) {
      run.status = "running";
    }

    const description = describeRunEvent(eventType, eventPayload, copy.value);
    if (description) {
      run.events.push({
        id: `${runId}-${eventType}-${createdAt}-${randomToken()}`,
        eventType,
        kind: eventKind,
        status: eventStatus || "completed",
        createdAt,
        payload: eventPayload,
        artifact: eventArtifact,
        ...description,
      });
      if (run.events.length > MAX_RUN_EVENTS) {
        run.events.splice(0, run.events.length - MAX_RUN_EVENTS);
      }
    }

    if (eventType === "run_part_delta" || eventType === "message_part_delta") {
      applyRunPartDelta(run, eventPayload, createdAt);
    }

    applyRunEventArtifact(run, eventArtifact);

    run.updatedAt = createdAt;
    session.updatedAt = createdAt;
    session.runs.sort((left, right) => right.updatedAt - left.updatedAt);
    sortSessions();
    if (isTerminalRunStatus(run.status) || eventType === "run_finished" || eventType === "run_failed") {
      scheduleRunSummaryFetch(session, run);
    }
  }

  function setNotice(text, tone) {
    state.notice.text = text;
    state.notice.tone = tone;
  }

  function setActiveSession(externalChatId) {
    state.activeExternalChatId = externalChatId;
    writeStoredValue(STORAGE_KEYS.activeExternalChatId, externalChatId);
    closeSidebar();
  }

  function getFirstWebSession() {
    return state.sessions.find((session) => !session.channel || session.channel === "web") || null;
  }

  function ensureActiveWebSession() {
    const session = currentSession.value;
    if (session && session.channel === "web") {
      return session;
    }
    let webSession = getFirstWebSession();
    if (!webSession) {
      webSession = createSession();
      state.sessions.unshift(webSession);
    }
    state.activeExternalChatId = webSession.externalChatId;
    writeStoredValue(STORAGE_KEYS.activeExternalChatId, webSession.externalChatId);
    return webSession;
  }

  function setSessionChannelFilter(value) {
    sessionChannelFilter.value = value === "web" ? "web" : "all";
    if (sessionChannelFilter.value !== "web") {
      return;
    }
    const session = currentSession.value;
    if (!session || session.channel === "web") {
      return;
    }
    const firstWebSession = getFirstWebSession();
    if (firstWebSession) {
      setActiveSession(firstWebSession.externalChatId);
    }
  }

  function selectRun(runId) {
    const session = currentSession.value;
    const normalizedRunId = String(runId || "").trim();
    if (!session || !normalizedRunId || !session.runs.some((run) => run.runId === normalizedRunId)) {
      return;
    }
    session.activeRunId = normalizedRunId;
    maybeLoadRunSummaryForSession(session);
    maybeLoadRunTraceForSession(session);
  }

  function persistActiveSession() {
    if (state.activeExternalChatId) {
      writeStoredValue(STORAGE_KEYS.activeExternalChatId, state.activeExternalChatId);
    }
  }

  function selectSettingsSection(sectionName) {
    settingsSection.value = Object.prototype.hasOwnProperty.call(copy.value.settingsTitles, sectionName) ? sectionName : "general";
    loadSettingsSection(settingsSection.value);
  }

  function syncSettingsForm() {
    settingsForm.wsUrl = state.wsUrl;
    settingsForm.displayName = state.displayName;
    settingsForm.externalChatId = currentSession.value?.externalChatId || "";
    settingsForm.showRunTimeline = state.showRunTimeline;
    settingsForm.showRunSummary = state.showRunSummary;
    settingsForm.showRunTrace = state.showRunTrace;
    settingsForm.language = state.language;
    settingsForm.colorScheme = state.colorScheme;
  }

  function openSettings(sectionName = "general") {
    settingsOpen.value = true;
    selectSettingsSection(sectionName);
    syncSettingsForm();
  }

  function closeSettings() {
    if (settingsOpen.value) {
      saveConnectionSettings();
    }
    cancelChannelConnect();
    cancelProviderConnect();
    settingsOpen.value = false;
  }

  function openSidebar() {
    sidebarOpen.value = true;
  }

  function closeSidebar() {
    sidebarOpen.value = false;
  }

  function toggleSidebar() {
    if (sidebarOpen.value) {
      closeSidebar();
      return;
    }
    openSidebar();
  }

  function toggleSidebarCollapsed() {
    sidebarCollapsed.value = !sidebarCollapsed.value;
    writeStoredValue(STORAGE_KEYS.sidebarCollapsed, String(sidebarCollapsed.value));
  }

  function disconnectSocket(reason, tone = "warning") {
    const socket = activeSocket;
    activeSocket = null;
    state.connectionState = "disconnected";
    if (socket && socket.readyState !== WebSocket.CLOSED) {
      socket.close(1000, "Client disconnect");
    }
    setNotice(reason, tone);
  }

  function buildSocketUrl(baseUrl, externalChatId) {
    const url = new URL(baseUrl);
    url.searchParams.set("external_chat_id", externalChatId);
    return url.toString();
  }

  async function requestSettingsJson(pathname, options = {}) {
    const [apiPathname, queryString] = String(pathname).split("?", 2);
    const url = buildHttpApiUrl(state.wsUrl, apiPathname);
    url.search = queryString || "";
    const response = await fetch(url.toString(), {
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    if (!response.ok) {
      const text = await response.text();
      const error = new Error(text || `HTTP ${response.status}`);
      error.status = response.status;
      error.statusText = response.statusText;
      throw error;
    }
    return response.json();
  }

  function normalizePermissionRequest(payload) {
    const requestId = String(payload?.request_id || payload?.requestId || "").trim();
    if (!requestId) {
      return null;
    }
    return {
      requestId,
      toolName: String(payload?.tool_name || payload?.toolName || copy.value.run.unknownTool).trim() || copy.value.run.unknownTool,
      reason: String(payload?.reason || "").trim(),
      status: String(payload?.status || "pending").trim() || "pending",
      actionType: String(payload?.action_type || payload?.actionType || "").trim(),
      riskLevel: String(payload?.risk_level || payload?.riskLevel || "").trim(),
      riskLevels: Array.isArray(payload?.risk_levels) ? payload.risk_levels.map((item) => String(item || "").trim()).filter(Boolean) : [],
      resource: String(payload?.resource || "").trim(),
      preview: String(payload?.preview || "").trim(),
      recommendedDecision: String(payload?.recommended_decision || payload?.recommendedDecision || "").trim(),
      sessionId: String(payload?.session_id || payload?.sessionId || "").trim(),
      externalChatId: String(payload?.external_chat_id || payload?.externalChatId || "").trim(),
      createdAt: normalizeEventTimestamp(payload?.created_at ?? payload?.createdAt),
      params: payload?.params && typeof payload.params === "object" ? payload.params : {},
    };
  }

  async function loadPermissionRequests() {
    permissionState.loading = true;
    permissionState.error = "";
    try {
      const payload = await requestSettingsJson("/api/permissions");
      permissionState.requests = Array.isArray(payload?.permissions)
        ? payload.permissions.map(normalizePermissionRequest).filter(Boolean)
        : [];
    } catch (error) {
      permissionState.error = error?.message || copy.value.permissions.loadFailed;
    } finally {
      permissionState.loading = false;
    }
  }

  async function resolvePermissionRequest(request, decision) {
    if (!request?.requestId || !["approve", "deny"].includes(decision)) {
      return;
    }
    permissionState.resolvingIds[request.requestId] = true;
    try {
      await requestSettingsJson(`/api/permissions/${encodeURIComponent(request.requestId)}/${decision}`, {
        method: "POST",
        body: JSON.stringify({ reason: "" }),
      });
      permissionState.requests = permissionState.requests.filter((entry) => entry.requestId !== request.requestId);
      setNotice(
        decision === "approve" ? copy.value.permissions.approved(request.toolName) : copy.value.permissions.denied(request.toolName),
        decision === "approve" ? "success" : "warning",
      );
      void loadCurrentSessionRuns({ force: true });
    } catch (error) {
      setNotice(error?.message || copy.value.permissions.resolveFailed, "error");
      void loadPermissionRequests();
    } finally {
      delete permissionState.resolvingIds[request.requestId];
    }
  }

  function normalizeTraceEvent(event) {
    const eventType = String(event?.event_type || event?.eventType || "run_event");
    const createdAt = normalizeEventTimestamp(event?.created_at ?? event?.createdAt);
    const eventPayload = coerceEventPayload(event?.payload);
    const kind = normalizeRunKind(event?.kind, inferRunEventKind(eventType));
    const status = String(event?.status || inferRunEventStatus(eventType, eventPayload)).trim() || "completed";
    const eventId = String(event?.event_id || event?.eventId || `${eventType}-${createdAt}-${randomToken()}`);
    return {
      id: eventId,
      schemaVersion: coerceNonNegativeInteger(event?.schema_version ?? event?.schemaVersion),
      eventType,
      kind,
      status,
      createdAt,
      payload: eventPayload,
      artifact: normalizeRunArtifact(event?.artifact, {
        kind,
        status,
        source: "event",
        sourceId: eventId,
        createdAt,
      }),
    };
  }

  function normalizeTracePart(part) {
    if (!part || typeof part !== "object") {
      return null;
    }
    const partId = String(part.part_id || part.partId || "").trim();
    const partType = String(part.part_type || part.partType || "part").trim() || "part";
    const createdAt = normalizeEventTimestamp(part.created_at ?? part.createdAt);
    const kind = normalizeRunKind(part.kind, partType.startsWith("tool_") ? "tool" : "other");
    const state = String(part.state || part.status || "completed").trim() || "completed";
    return {
      partId,
      partType,
      schemaVersion: coerceNonNegativeInteger(part.schema_version ?? part.schemaVersion),
      kind,
      state,
      content: String(part.content || ""),
      toolName: String(part.tool_name || part.toolName || "").trim(),
      metadata: part.metadata && typeof part.metadata === "object" ? part.metadata : {},
      artifact: normalizeRunArtifact(part.artifact, {
        kind,
        status: state,
        source: "part",
        sourceId: partId,
        artifactType: partType,
        createdAt,
      }),
      createdAt,
    };
  }

  function applyRunPartDelta(run, payload, createdAt) {
    const partType = String(payload.part_type || payload.partType || "assistant_message").trim() || "assistant_message";
    const partId = String(payload.part_id || payload.partId || `stream:${run.runId}:${partType}`).trim();
    const delta = String(payload.content_delta ?? payload.delta ?? payload.text ?? payload.content ?? "");
    const existingIndex = run.parts.findIndex((part) => part.partId === partId);
    const existing = existingIndex >= 0 ? run.parts[existingIndex] : null;
    const nextState = String(payload.state || payload.status || existing?.state || "running").trim() || "running";
    if (!delta && !existing) {
      return;
    }

    const metadata = payload.metadata && typeof payload.metadata === "object" ? payload.metadata : {};
    const nextPart = normalizeTracePart({
      part_id: partId,
      part_type: partType,
      kind: payload.kind || existing?.kind || "text",
      state: nextState,
      content: `${existing?.content || ""}${delta}`,
      tool_name: payload.tool_name || payload.toolName || existing?.toolName || "",
      metadata: { ...(existing?.metadata || {}), ...metadata, streaming: !TERMINAL_PART_STATES.has(nextState) },
      created_at: existing?.createdAt || createdAt,
    });
    if (!nextPart) {
      return;
    }
    if (existingIndex >= 0) {
      run.parts[existingIndex] = nextPart;
    } else {
      run.parts.push(nextPart);
    }
    if (run.parts.length > MAX_RUN_ARTIFACTS) {
      run.parts.splice(0, run.parts.length - MAX_RUN_ARTIFACTS);
    }
    applyRunEventArtifact(run, nextPart.artifact);
  }

  function normalizeTraceFileChange(change) {
    const path = String(change?.path || "").trim();
    if (!path) {
      return null;
    }
    const beforeContent = change?.before_content ?? change?.beforeContent ?? null;
    const afterContent = change?.after_content ?? change?.afterContent ?? null;
    const createdAt = normalizeEventTimestamp(change?.created_at ?? change?.createdAt);
    return {
      changeId: String(change?.change_id || change?.changeId || "").trim(),
      schemaVersion: coerceNonNegativeInteger(change?.schema_version ?? change?.schemaVersion),
      kind: normalizeRunKind(change?.kind, "file"),
      state: String(change?.state || change?.status || "completed").trim() || "completed",
      path,
      action: String(change?.action || "").trim(),
      toolName: String(change?.tool_name || change?.toolName || "").trim(),
      diffLen: coerceNonNegativeInteger(change?.diff_len ?? change?.diffLen),
      diff: String(change?.diff || ""),
      beforeContent,
      afterContent,
      snapshotsAvailable: {
        before: coerceBoolean(change?.snapshots_available?.before ?? change?.snapshotsAvailable?.before ?? beforeContent !== null),
        after: coerceBoolean(change?.snapshots_available?.after ?? change?.snapshotsAvailable?.after ?? afterContent !== null),
      },
      artifact: normalizeRunArtifact(change?.artifact, {
        kind: "file",
        status: "completed",
        source: "file_change",
        sourceId: change?.change_id || change?.changeId || "",
        artifactType: "file_change",
        createdAt,
      }),
      createdAt,
    };
  }

  function localizeRawRunEvents(rawEvents) {
    return rawEvents
      .map((event) => {
        const description = describeRunEvent(event.eventType, event.payload, copy.value);
        return description
          ? {
              id: `${event.id}-localized`,
              eventType: event.eventType,
              createdAt: event.createdAt,
              payload: event.payload,
              ...description,
            }
          : null;
      })
      .filter(Boolean)
      .slice(-MAX_RUN_EVENTS);
  }

  function runSummaryTimerKey(sessionId, runId) {
    return `${sessionId}\u0000${runId}`;
  }

  function clearRunSummaryTimer(sessionId, runId) {
    const key = runSummaryTimerKey(sessionId, runId);
    const timer = runSummaryTimers.get(key);
    if (timer) {
      clearTimeout(timer);
      runSummaryTimers.delete(key);
    }
  }

  async function loadRunSummary(session, run) {
    const sessionId = run?.sessionId || session?.sessionId || "";
    if (!state.showRunSummary || !sessionId || !run?.runId || clientDisposed) {
      return;
    }

    clearRunSummaryTimer(sessionId, run.runId);
    run.summaryLoading = true;
    run.summaryError = "";
    try {
      const payload = await requestSettingsJson(buildRunSummaryPath(run.runId, sessionId));
      const summary = normalizeRunSummary(payload);
      if (summary) {
        run.summary = summary;
        run.status = summary.status || run.status;
        run.summaryNotFoundAttempts = 0;
        maybeLoadRunTraceForSession(session);
      }
    } catch (error) {
      if (error?.status === 404) {
        run.summaryNotFoundAttempts = coerceNonNegativeInteger(run.summaryNotFoundAttempts) + 1;
        run.summaryError = "";
        if (run.summaryNotFoundAttempts < RUN_SUMMARY_NOT_FOUND_RETRY_LIMIT) {
          scheduleRunSummaryRetry(session, run);
        }
        return;
      }
      run.summaryError = error?.message || copy.value.notices.runSummaryLoadFailed;
    } finally {
      run.summaryLoading = false;
    }
  }

  function scheduleRunSummaryRetry(session, run) {
    const sessionId = run?.sessionId || session?.sessionId || "";
    if (!state.showRunSummary || !sessionId || !run?.runId || run.summary || clientDisposed) {
      return;
    }

    clearRunSummaryTimer(sessionId, run.runId);
    const key = runSummaryTimerKey(sessionId, run.runId);
    const timer = setTimeout(() => {
      runSummaryTimers.delete(key);
      void loadRunSummary(session, run);
    }, RUN_SUMMARY_NOT_FOUND_RETRY_DELAY_MS);
    runSummaryTimers.set(key, timer);
  }

  async function loadRunTrace(session, run) {
    const sessionId = run?.sessionId || session?.sessionId || "";
    if (!sessionId || !run?.runId || clientDisposed) {
      return;
    }

    run.traceLoading = true;
    run.traceError = "";
    try {
      const payload = await requestSettingsJson(buildRunTracePath(run.runId, sessionId));
      const rawEvents = Array.isArray(payload?.events)
        ? compactRunEvents(payload.events.map(normalizeTraceEvent))
        : [];
      const fileChanges = Array.isArray(payload?.file_changes || payload?.fileChanges)
        ? (payload.file_changes || payload.fileChanges).map(normalizeTraceFileChange).filter(Boolean)
        : [];
      const parts = Array.isArray(payload?.parts)
        ? payload.parts.map(normalizeTracePart).filter(Boolean)
        : [];
      const artifacts = Array.isArray(payload?.artifacts)
        ? payload.artifacts.map((artifact) => normalizeRunArtifact(artifact)).filter(Boolean)
        : [];
      run.rawEvents = rawEvents;
      run.eventCounts = normalizeTraceEventCounts(payload?.event_counts || payload?.eventCounts, rawEvents);
      run.events = localizeRawRunEvents(rawEvents);
      run.parts = parts;
      run.artifacts = artifacts.length
        ? artifacts.slice(-MAX_RUN_ARTIFACTS)
        : [
            ...rawEvents.map((event) => event.artifact).filter(Boolean),
            ...parts.map((part) => part.artifact).filter(Boolean),
            ...fileChanges.map((change) => change.artifact).filter(Boolean),
          ].slice(-MAX_RUN_ARTIFACTS);
      run.artifacts.forEach((artifact) => applyToolArtifactToParts(run, artifact));
      run.fileChanges = fileChanges;
      run.diffSummary = normalizeDiffSummary(payload?.diff_summary || payload?.diffSummary);
      run.worktreeSandbox = findWorktreeSandbox(parts, run.artifacts);
      run.traceLoaded = true;
    } catch (error) {
      run.traceError = error?.message || copy.value.notices.runTraceLoadFailed;
    } finally {
      run.traceLoading = false;
    }
  }

  function scheduleRunSummaryFetch(session, run) {
    const sessionId = run?.sessionId || session?.sessionId || "";
    if (!state.showRunSummary || !sessionId || !run?.runId) {
      return;
    }

    clearRunSummaryTimer(sessionId, run.runId);
    run.summaryError = "";
    run.summaryLoading = true;
    const key = runSummaryTimerKey(sessionId, run.runId);
    const timer = setTimeout(() => {
      runSummaryTimers.delete(key);
      void loadRunSummary(session, run);
    }, RUN_SUMMARY_FETCH_DELAY_MS);
    runSummaryTimers.set(key, timer);
  }

  function maybeLoadRunSummaryForSession(session) {
    const run = getActiveRun(session);
    if (!shouldLoadRunSummary(state, run)) {
      return;
    }
    scheduleRunSummaryFetch(session, run);
  }

  function maybeLoadRunTraceForSession(session) {
    const run = getActiveRun(session);
    if (!shouldLoadRunTrace(run)) {
      return;
    }
    void loadRunTrace(session, run);
  }

  function getActiveCronSessionId() {
    const session = currentSession.value;
    if (session?.sessionId) {
      return session.sessionId;
    }
    if (session?.externalChatId) {
      return `web:${session.externalChatId}`;
    }
    return "";
  }

  function formatDateTimeLocal(timestampMs) {
    const date = new Date(Number(timestampMs || 0));
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    const offsetMs = date.getTimezoneOffset() * 60_000;
    return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
  }

  function resetCronJobForm() {
    settingsState.cronJobForm.showEditor = false;
    settingsState.cronJobForm.sessionId = "";
    settingsState.cronJobForm.jobId = "";
    settingsState.cronJobForm.mode = "cron";
    settingsState.cronJobForm.name = "";
    settingsState.cronJobForm.message = "";
    settingsState.cronJobForm.everySeconds = "3600";
    settingsState.cronJobForm.cronExpr = "0 9 * * *";
    settingsState.cronJobForm.at = "";
    settingsState.cronJobForm.timezone = settingsState.schedule.default_timezone || "UTC";
    settingsState.cronJobForm.deliver = true;
  }

  function buildCronJobPayload() {
    const form = settingsState.cronJobForm;
    const payload = {
      session_id: form.sessionId || getActiveCronSessionId(),
      kind: form.mode,
      name: String(form.name || "").trim(),
      message: String(form.message || "").trim(),
      deliver: Boolean(form.deliver),
    };
    if (form.mode === "every") {
      payload.every_seconds = Number(form.everySeconds);
    } else if (form.mode === "cron") {
      payload.cron_expr = String(form.cronExpr || "").trim();
      payload.tz = String(form.timezone || settingsState.schedule.default_timezone || "UTC").trim();
    } else if (form.mode === "at") {
      payload.at = String(form.at || "").trim();
    }
    return payload;
  }

  function parseLines(value) {
    return String(value || "")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function parseListText(value, fallback = []) {
    const items = String(value || "")
      .replace(/,/g, "\n")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
    return items.length ? items : fallback;
  }

  function parseOptionalJsonObject(value, fieldLabel) {
    const text = String(value || "").trim();
    if (!text) {
      return null;
    }
    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("not object");
      }
      return parsed;
    } catch {
      settingsState.mcpError = copy.value.notices.mcpJsonInvalid(fieldLabel);
      return undefined;
    }
  }

  function formatJsonObject(value) {
    return value && typeof value === "object" && !Array.isArray(value)
      ? JSON.stringify(value, null, 2)
      : "";
  }

  function formatListField(value, fallback = "") {
    if (Array.isArray(value)) {
      return value.map((item) => String(item || "").trim()).filter(Boolean).join("\n");
    }
    if (typeof value === "string") {
      return value.trim();
    }
    return fallback;
  }

  function normalizeMcpTransport(value, fallback = "stdio") {
    const transport = String(value || "").trim();
    if (MCP_TRANSPORT_TYPES.has(transport)) {
      return transport;
    }
    if (["streamable-http", "streamable_http", "http"].includes(transport)) {
      return "streamableHttp";
    }
    return fallback;
  }

  function getMcpServerMap(parsed) {
    if (parsed?.mcpServers && typeof parsed.mcpServers === "object" && !Array.isArray(parsed.mcpServers)) {
      return parsed.mcpServers;
    }
    if (parsed?.mcp_servers && typeof parsed.mcp_servers === "object" && !Array.isArray(parsed.mcp_servers)) {
      return parsed.mcp_servers;
    }
    if (parsed?.servers && typeof parsed.servers === "object" && !Array.isArray(parsed.servers)) {
      return parsed.servers;
    }
    return null;
  }

  function extractMcpServerFromJson(parsed) {
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      settingsState.mcpError = copy.value.notices.mcpJsonInvalid(copy.value.settings.mcp.configJson);
      return null;
    }

    const serverMap = getMcpServerMap(parsed);
    if (serverMap) {
      const entries = Object.entries(serverMap).filter(([, value]) => value && typeof value === "object" && !Array.isArray(value));
      if (entries.length !== 1) {
        settingsState.mcpError = copy.value.notices.mcpJsonSingleServer;
        return null;
      }
      const [serverId, server] = entries[0];
      return { serverId, server };
    }

    if (Array.isArray(parsed.servers)) {
      if (parsed.servers.length !== 1 || !parsed.servers[0] || typeof parsed.servers[0] !== "object") {
        settingsState.mcpError = copy.value.notices.mcpJsonSingleServer;
        return null;
      }
      const server = parsed.servers[0];
      return { serverId: server.id || server.name || server.server_id || server.server_name || "", server };
    }

    if (parsed.server && typeof parsed.server === "object" && !Array.isArray(parsed.server)) {
      return { serverId: parsed.server_name || parsed.server_id || parsed.id || parsed.name || "", server: parsed.server };
    }

    return {
      serverId: parsed.server_id || parsed.serverId || parsed.server_name || parsed.serverName || parsed.id || parsed.name || "",
      server: parsed,
    };
  }

  function normalizeMcpSettings(payload) {
    return {
      ...payload,
      servers: Array.isArray(payload?.servers) ? payload.servers : [],
      runtime: payload?.runtime && typeof payload.runtime === "object"
        ? {
            connected: Boolean(payload.runtime.connected),
            connecting: Boolean(payload.runtime.connecting),
            connect_failures: Number(payload.runtime.connect_failures || 0),
            retry_after: Number(payload.runtime.retry_after || 0),
            tool_names: Array.isArray(payload.runtime.tool_names) ? payload.runtime.tool_names : [],
          }
        : settingsState.mcp.runtime,
    };
  }

  function resetMcpForm() {
    settingsState.mcpForm.showEditor = false;
    settingsState.mcpForm.editingId = "";
    settingsState.mcpForm.serverId = "";
    settingsState.mcpForm.type = "stdio";
    settingsState.mcpForm.command = "";
    settingsState.mcpForm.argsText = "";
    settingsState.mcpForm.url = "";
    settingsState.mcpForm.envJson = "";
    settingsState.mcpForm.headersJson = "";
    settingsState.mcpForm.toolTimeout = "30";
    settingsState.mcpForm.enabledToolsText = "*";
    settingsState.mcpForm.showAdvanced = false;
    settingsState.mcpForm.showJsonInput = false;
    settingsState.mcpForm.jsonText = "";
  }

  function buildMcpServerPayload() {
    settingsState.mcpError = "";
    const form = settingsState.mcpForm;
    const env = parseOptionalJsonObject(form.envJson, copy.value.settings.mcp.env);
    if (env === undefined) {
      return null;
    }
    const headers = parseOptionalJsonObject(form.headersJson, copy.value.settings.mcp.headers);
    if (headers === undefined) {
      return null;
    }

    const payload = {
      server_id: String(form.serverId || "").trim(),
      type: form.type,
      command: String(form.command || "").trim(),
      args: parseLines(form.argsText),
      url: String(form.url || "").trim(),
      tool_timeout: Number(form.toolTimeout || 30),
      enabled_tools: parseListText(form.enabledToolsText, ["*"]),
    };
    if (env !== null) {
      payload.env = env;
    }
    if (headers !== null) {
      payload.headers = headers;
    }
    return payload;
  }

  function makeHistoryMessage(message, index) {
    const metadata = message?.metadata && typeof message.metadata === "object" ? message.metadata : {};
    const role = message?.role === "user" ? "user" : "assistant";
    return {
      id: `history-${normalizeEventTimestamp(message?.created_at)}-${index}-${randomToken()}`,
      role,
      text: String(message?.content || ""),
      meta: metadata.sender_name || metadata.sender_id || (role === "user" ? state.displayName : "OpenSprite"),
      createdAt: normalizeEventTimestamp(message?.created_at),
    };
  }

  function normalizeSessionEntryContent(item, index) {
    if (!item || typeof item !== "object") {
      return null;
    }
    const type = String(item.type || "text").trim() || "text";
    const artifact = item.artifact && typeof item.artifact === "object" ? normalizeRunArtifact(item.artifact) : null;
    return {
      id: String(item.part_id || item.partId || item.artifact_id || item.artifactId || `${type}-${index}`).trim(),
      type,
      status: String(item.status || "").trim(),
      title: String(item.title || artifact?.title || type).trim(),
      detail: String(item.detail || item.text || artifact?.detail || "").trim(),
      text: String(item.text || ""),
      createdAt: normalizeEventTimestamp(item.created_at ?? item.createdAt),
      artifact,
    };
  }

  function makeHistoryEntry(entry, index) {
    if (!entry || typeof entry !== "object") {
      return null;
    }
    const role = entry.role === "user" ? "user" : "assistant";
    const content = Array.isArray(entry.content)
      ? entry.content.map(normalizeSessionEntryContent).filter(Boolean)
      : [];
    return {
      id: String(entry.entry_id || entry.entryId || `entry-${index}-${randomToken()}`).trim(),
      type: String(entry.entry_type || entry.entryType || role).trim() || role,
      role,
      runId: String(entry.run_id || entry.runId || "").trim(),
      status: String(entry.status || "").trim(),
      text: String(entry.text || ""),
      content,
      meta: entry.metadata?.sender_name || entry.metadata?.sender_id || (role === "user" ? state.displayName : "OpenSprite"),
      createdAt: normalizeEventTimestamp(entry.created_at ?? entry.createdAt),
      updatedAt: normalizeEventTimestamp(entry.updated_at ?? entry.updatedAt),
      metadata: entry.metadata && typeof entry.metadata === "object" ? entry.metadata : {},
    };
  }

  function normalizeHistoryRun(payload) {
    const runId = String(payload?.run_id || payload?.runId || "").trim();
    if (!runId) {
      return null;
    }
    const finishedAt = Number(payload?.finished_at ?? payload?.finishedAt);
    return createRunViewState({
      runId,
      sessionId: String(payload?.session_id || payload?.sessionId || "").trim(),
      status: String(payload?.status || "running").trim() || "running",
      createdAt: normalizeEventTimestamp(payload?.created_at ?? payload?.createdAt),
      updatedAt: normalizeEventTimestamp(payload?.updated_at ?? payload?.updatedAt),
      finishedAt: Number.isFinite(finishedAt) && finishedAt > 0 ? normalizeEventTimestamp(finishedAt) : null,
    });
  }

  function mergeSessionRuns(session, runs) {
    const existingRuns = new Map((session.runs || []).map((run) => [run.runId, run]));
    const mergedRuns = [];

    for (const run of runs) {
      const existing = existingRuns.get(run.runId);
      if (existing) {
        existing.sessionId = existing.sessionId || run.sessionId;
        existing.status = run.status || existing.status;
        existing.createdAt = run.createdAt || existing.createdAt;
        existing.updatedAt = Math.max(Number(existing.updatedAt || 0), Number(run.updatedAt || 0));
        existing.finishedAt = run.finishedAt || existing.finishedAt;
        mergedRuns.push(existing);
        existingRuns.delete(run.runId);
      } else {
        mergedRuns.push(run);
      }
    }

    for (const run of existingRuns.values()) {
      if (run.status === "running" || run.summary || run.rawEvents?.length) {
        mergedRuns.push(run);
      }
    }

    session.runs = mergedRuns.sort((left, right) => Number(right.updatedAt || 0) - Number(left.updatedAt || 0));
    if (!session.runs.some((run) => run.runId === session.activeRunId)) {
      session.activeRunId = session.runs[0]?.runId || null;
    }
  }

  async function loadCurrentSessionRuns({ force = false } = {}) {
    const session = currentSession.value;
    if (!session?.sessionId || session.runsLoading || (session.runsLoaded && !force)) {
      return;
    }

    session.runsLoading = true;
    session.runsError = "";
    try {
      const payload = await requestSettingsJson(buildRunsPath(session.sessionId));
      const runs = Array.isArray(payload?.runs)
        ? payload.runs.map(normalizeHistoryRun).filter(Boolean)
        : [];
      mergeSessionRuns(session, runs);
      session.runsLoaded = true;
      maybeLoadRunSummaryForSession(session);
      maybeLoadRunTraceForSession(session);
    } catch (error) {
      session.runsError = error?.message || copy.value.notices.runHistoryLoadFailed;
    } finally {
      session.runsLoading = false;
    }
  }

  function shouldBackfillSessionRuns(session) {
    if (!session?.sessionId) {
      return false;
    }
    const now = Date.now();
    const lastBackfillAt = runBackfillTimes.get(session.sessionId) || 0;
    if (session.runsLoaded && now - lastBackfillAt < RUN_BACKFILL_COOLDOWN_MS) {
      return false;
    }
    runBackfillTimes.set(session.sessionId, now);
    return true;
  }

  function normalizeHistorySession(payload) {
    const sessionId = String(payload?.session_id || "").trim();
    const channel = String(payload?.channel || channelFromSessionId(sessionId) || "web").trim() || "web";
    const transportExternalChatId = String(payload?.external_chat_id || "").trim()
      || externalChatIdFromSessionId(sessionId)
      || generateExternalChatId();
    const externalChatId = channel === "web" ? transportExternalChatId : (sessionId || `${channel}:${transportExternalChatId}`);
    const session = createSession(externalChatId);
    session.channel = channel;
    session.transportExternalChatId = transportExternalChatId;
    session.sessionId = sessionId || null;
    session.title = String(payload?.title || "").trim() || "New chat";
    session.updatedAt = normalizeEventTimestamp(payload?.updated_at);
    session.messages = Array.isArray(payload?.messages)
      ? payload.messages.map(makeHistoryMessage).filter((message) => message.text.trim())
      : [];
    session.entries = Array.isArray(payload?.entries)
      ? payload.entries.map(makeHistoryEntry).filter(Boolean)
      : [];
    session.runs = Array.isArray(payload?.runs)
      ? payload.runs.map(normalizeHistoryRun).filter(Boolean)
      : [];
    session.activeRunId = session.runs[0]?.runId || null;
    session.workState = normalizeWorkState(payload?.work_state);
    const status = payload?.status && typeof payload.status === "object" ? payload.status : {};
    session.status = {
      status: String(status.status || "idle").trim() || "idle",
      updatedAt: normalizeEventTimestamp(status.updated_at ?? status.updatedAt),
      metadata: status.metadata && typeof status.metadata === "object" ? status.metadata : {},
    };
    return session;
  }

  function mergeHistorySessions(historySessions) {
    if (!historySessions.length) {
      return;
    }

    const sessionsByExternalChatId = new Map(historySessions.map((session) => [session.externalChatId, session]));
    for (const session of state.sessions) {
      if (!sessionsByExternalChatId.has(session.externalChatId) && (session.sessionId || session.messages.length > 0)) {
        sessionsByExternalChatId.set(session.externalChatId, session);
      }
    }

    state.sessions = [...sessionsByExternalChatId.values()].sort((left, right) => right.updatedAt - left.updatedAt);
    if (!state.sessions.some((session) => session.externalChatId === state.activeExternalChatId)) {
      state.activeExternalChatId = state.sessions[0]?.externalChatId || state.activeExternalChatId;
      writeStoredValue(STORAGE_KEYS.activeExternalChatId, state.activeExternalChatId);
    }
  }

  async function loadSessionHistory() {
    try {
      const payload = await requestSettingsJson("/api/sessions?channel=all&limit=50&messages=50");
      const historySessions = Array.isArray(payload.sessions)
        ? payload.sessions.map(normalizeHistorySession)
        : [];
      mergeHistorySessions(historySessions);
    } catch {
      setNotice(copy.value.notices.historyLoadFailed, "warning");
    }
  }

  async function loadProviderSettings() {
    settingsState.providersLoading = true;
    settingsState.providersError = "";
    try {
      settingsState.providers = await requestSettingsJson("/api/settings/providers");
    } catch (error) {
      settingsState.providersError = error?.message || copy.value.notices.providerLoadFailed;
    } finally {
      settingsState.providersLoading = false;
    }
  }

  function visibleChannels(channels) {
    return (channels || []).filter((channel) => channel.id !== "web" && channel.id !== "console");
  }

  function normalizeChannelSettings(payload) {
    const channels = visibleChannels(payload.channels);
    const hasGroupedChannels = Array.isArray(payload.connected) || Array.isArray(payload.available);
    if (hasGroupedChannels) {
      return {
        ...payload,
        connected: visibleChannels(payload.connected),
        available: visibleChannels(payload.available),
        channels,
      };
    }

    return {
      ...payload,
      connected: channels.filter((channel) => channel.token_configured),
      available: channels.filter((channel) => !channel.token_configured),
      channels,
    };
  }

  function sortChannelList(channels) {
    return [...channels].sort((left, right) => String(left.name || left.id).localeCompare(String(right.name || right.id)));
  }

  function upsertConnectedChannel(channel) {
    const visibleChannel = visibleChannels([channel])[0];
    if (!visibleChannel) {
      return;
    }
    const connected = settingsState.channels.connected.filter((entry) => entry.id !== visibleChannel.id);
    const nextConnected = sortChannelList([...connected, visibleChannel]);
    settingsState.channels = {
      ...settingsState.channels,
      connected: nextConnected,
      channels: nextConnected,
    };
  }

  function removeConnectedChannel(channelId) {
    const nextConnected = settingsState.channels.connected.filter((entry) => entry.id !== channelId);
    settingsState.channels = {
      ...settingsState.channels,
      connected: nextConnected,
      channels: nextConnected,
    };
  }

  async function loadChannelSettings() {
    settingsState.channelsLoading = true;
    settingsState.channelsError = "";
    try {
      const payload = await requestSettingsJson("/api/settings/channels");
      settingsState.channels = normalizeChannelSettings(payload);
    } catch (error) {
      settingsState.channelsError = error?.message || copy.value.notices.channelLoadFailed;
    } finally {
      settingsState.channelsLoading = false;
    }
  }

  async function loadModelSettings() {
    settingsState.modelsLoading = true;
    settingsState.modelsError = "";
    try {
      settingsState.models = await requestSettingsJson("/api/settings/models");
      for (const provider of settingsState.models.providers || []) {
        const selectedModel = provider.selected_model || provider.models?.[0] || "";
        settingsState.modelSelections[provider.id] = selectedModel;
        if (!Object.prototype.hasOwnProperty.call(settingsState.customModels, provider.id)) {
          settingsState.customModels[provider.id] = "";
        }
      }
    } catch (error) {
      settingsState.modelsError = error?.message || copy.value.notices.modelLoadFailed;
    } finally {
      settingsState.modelsLoading = false;
    }
  }

  async function loadMcpSettings() {
    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    try {
      settingsState.mcp = normalizeMcpSettings(await requestSettingsJson("/api/settings/mcp"));
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpLoadFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  async function loadScheduleSettings() {
    settingsState.scheduleLoading = true;
    settingsState.scheduleError = "";
    try {
      const payload = await requestSettingsJson("/api/settings/schedule");
      settingsState.schedule = payload;
      settingsState.scheduleForm.defaultTimezone = payload.default_timezone || "UTC";
      if (!settingsState.cronJobForm.timezone || !settingsState.cronJobForm.jobId) {
        settingsState.cronJobForm.timezone = settingsState.scheduleForm.defaultTimezone;
      }
    } catch (error) {
      settingsState.scheduleError = error?.message || copy.value.notices.scheduleLoadFailed;
    } finally {
      settingsState.scheduleLoading = false;
    }
  }

  async function loadCronJobs() {
    settingsState.cronJobsLoading = true;
    settingsState.cronJobsError = "";
    try {
      const payload = await requestSettingsJson("/api/cron/jobs");
      settingsState.cronJobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    } catch (error) {
      settingsState.cronJobsError = error?.message || copy.value.notices.cronJobsLoadFailed;
    } finally {
      settingsState.cronJobsLoading = false;
    }
  }

  function loadSettingsSection(sectionName) {
    if (sectionName === "channels") {
      loadChannelSettings();
      return;
    }
    if (sectionName === "providers") {
      loadProviderSettings();
      return;
    }
    if (sectionName === "models") {
      loadModelSettings();
      return;
    }
    if (sectionName === "mcp") {
      loadMcpSettings();
      return;
    }
    if (sectionName === "schedule") {
      loadScheduleSettings();
      loadCronJobs();
    }
  }

  function beginChannelConnect(channel) {
    settingsState.channelsNotice = "";
    settingsState.channelsError = "";
    cancelProviderConnect();
    settingsState.channelConnectForm.type = channel.type || channel.id;
    settingsState.channelConnectForm.name = channel.name || "";
    settingsState.channelConnectForm.token = "";
  }

  function cancelChannelConnect() {
    settingsState.channelConnectForm.type = "";
    settingsState.channelConnectForm.name = "";
    settingsState.channelConnectForm.token = "";
  }

  async function saveChannelConnection() {
    const channelType = settingsState.channelConnectForm.type;
    if (!channelType) {
      return;
    }
    settingsState.channelsLoading = true;
    settingsState.channelsError = "";
    settingsState.channelsNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/channels", {
        method: "POST",
        body: JSON.stringify({
          type: channelType,
          name: settingsState.channelConnectForm.name,
          token: settingsState.channelConnectForm.token,
        }),
      });
      settingsState.channelsNotice = copy.value.notices.channelConnected(payload.channel.name, payload.restart_required);
      upsertConnectedChannel(payload.channel);
      cancelChannelConnect();
      await loadChannelSettings();
    } catch (error) {
      settingsState.channelsError = error?.message || copy.value.notices.channelConnectFailed;
    } finally {
      settingsState.channelsLoading = false;
    }
  }

  async function disconnectChannel(channel) {
    settingsState.channelsLoading = true;
    settingsState.channelsError = "";
    settingsState.channelsNotice = "";
    try {
      const payload = await requestSettingsJson(`/api/settings/channels/${encodeURIComponent(channel.id)}/disconnect`, {
        method: "POST",
      });
      settingsState.channelsNotice = copy.value.notices.channelDisconnected(channel.name, payload.restart_required);
      removeConnectedChannel(channel.id);
      await loadChannelSettings();
    } catch (error) {
      settingsState.channelsError = error?.message || copy.value.notices.channelDisconnectFailed;
    } finally {
      settingsState.channelsLoading = false;
    }
  }

  function beginProviderConnect(provider) {
    settingsState.providersNotice = "";
    settingsState.providersError = "";
    cancelChannelConnect();
    settingsState.connectForm.providerId = provider.id;
    settingsState.connectForm.apiKey = "";
    settingsState.connectForm.baseUrl = provider.default_base_url || provider.base_url || "";
    settingsState.connectForm.showAdvanced = false;
  }

  function cancelProviderConnect() {
    settingsState.connectForm.providerId = "";
    settingsState.connectForm.apiKey = "";
    settingsState.connectForm.baseUrl = "";
    settingsState.connectForm.showAdvanced = false;
  }

  async function saveProviderConnection() {
    const providerId = settingsState.connectForm.providerId;
    if (!providerId) {
      return;
    }
    settingsState.providersLoading = true;
    settingsState.providersError = "";
    settingsState.providersNotice = "";
    try {
      await requestSettingsJson(`/api/settings/providers/${encodeURIComponent(providerId)}/connect`, {
        method: "PUT",
        body: JSON.stringify({
          api_key: settingsState.connectForm.apiKey,
          base_url: settingsState.connectForm.baseUrl,
        }),
      });
      settingsState.providersNotice = copy.value.notices.providerConnected;
      cancelProviderConnect();
      await loadProviderSettings();
      await loadModelSettings();
    } catch (error) {
      settingsState.providersError = error?.message || copy.value.notices.providerConnectFailed;
    } finally {
      settingsState.providersLoading = false;
    }
  }

  async function disconnectProvider(provider) {
    settingsState.providersLoading = true;
    settingsState.providersError = "";
    settingsState.providersNotice = "";
    try {
      const payload = await requestSettingsJson(`/api/settings/providers/${encodeURIComponent(provider.id)}/disconnect`, {
        method: "POST",
      });
      settingsState.providersNotice = copy.value.notices.providerDisconnected(provider.name, payload.restart_required);
      await loadProviderSettings();
      await loadModelSettings();
    } catch (error) {
      settingsState.providersError = error?.message || copy.value.notices.providerDisconnectFailed;
    } finally {
      settingsState.providersLoading = false;
    }
  }

  async function selectModel(providerId, model) {
    const normalizedModel = String(model || "").trim();
    if (!normalizedModel) {
      settingsState.modelsError = copy.value.notices.modelRequired;
      return;
    }

    settingsState.modelsLoading = true;
    settingsState.modelsError = "";
    settingsState.modelsNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/models/select", {
        method: "POST",
        body: JSON.stringify({ provider_id: providerId, model: normalizedModel }),
      });
      settingsState.modelsNotice = payload.restart_required
        ? copy.value.notices.modelRestartRequired
        : copy.value.notices.modelApplied;
      settingsState.customModels[providerId] = "";
      settingsState.modelSelections[providerId] = normalizedModel;
      await loadModelSettings();
      await loadProviderSettings();
    } catch (error) {
      settingsState.modelsError = error?.message || copy.value.notices.modelSelectFailed;
    } finally {
      settingsState.modelsLoading = false;
    }
  }

  function beginMcpEdit(server) {
    settingsState.mcpNotice = "";
    settingsState.mcpError = "";
    settingsState.mcpForm.showEditor = true;
    settingsState.mcpForm.editingId = server.id;
    settingsState.mcpForm.serverId = server.id;
    settingsState.mcpForm.type = server.type || "stdio";
    settingsState.mcpForm.command = server.command || "";
    settingsState.mcpForm.argsText = Array.isArray(server.args) ? server.args.join("\n") : "";
    settingsState.mcpForm.url = server.url || "";
    settingsState.mcpForm.envJson = "";
    settingsState.mcpForm.headersJson = "";
    settingsState.mcpForm.toolTimeout = String(server.tool_timeout || 30);
    settingsState.mcpForm.enabledToolsText = Array.isArray(server.enabled_tools) ? server.enabled_tools.join("\n") : "*";
    settingsState.mcpForm.showAdvanced = false;
    settingsState.mcpForm.showJsonInput = false;
    settingsState.mcpForm.jsonText = "";
  }

  function cancelMcpEdit() {
    resetMcpForm();
  }

  function beginMcpCreate() {
    resetMcpForm();
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    settingsState.mcpForm.showEditor = true;
  }

  function toggleMcpAdvanced() {
    settingsState.mcpForm.showAdvanced = !settingsState.mcpForm.showAdvanced;
  }

  function toggleMcpJsonInput() {
    settingsState.mcpForm.showJsonInput = !settingsState.mcpForm.showJsonInput;
  }

  function toggleMcpToolGroup(serverId) {
    const key = String(serverId || "").trim() || "unknown";
    settingsState.mcpToolGroupsExpanded[key] = settingsState.mcpToolGroupsExpanded[key] !== true;
  }

  function applyMcpJson() {
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    let parsed;
    try {
      parsed = JSON.parse(String(settingsState.mcpForm.jsonText || ""));
    } catch {
      settingsState.mcpError = copy.value.notices.mcpJsonInvalid(copy.value.settings.mcp.configJson);
      return;
    }

    const extracted = extractMcpServerFromJson(parsed);
    if (!extracted) {
      return;
    }

    const form = settingsState.mcpForm;
    const server = extracted.server;
    const nextServerId = String(extracted.serverId || "").trim();
    if (form.editingId && nextServerId && nextServerId !== form.editingId) {
      settingsState.mcpError = copy.value.notices.mcpJsonEditingMismatch;
      return;
    }
    if (!form.editingId && nextServerId) {
      form.serverId = nextServerId;
    }

    const rawType = server.type || server.transport_type || server.transport;
    form.type = normalizeMcpTransport(rawType, server.url ? "streamableHttp" : "stdio");
    form.command = String(server.command || "").trim();
    form.argsText = formatListField(server.args, form.argsText);
    form.url = String(server.url || "").trim();
    form.toolTimeout = String(server.tool_timeout || server.toolTimeout || form.toolTimeout || 30);
    form.enabledToolsText = formatListField(server.enabled_tools || server.enabledTools, form.enabledToolsText || "*") || "*";
    form.envJson = formatJsonObject(server.env) || form.envJson;
    form.headersJson = formatJsonObject(server.headers) || form.headersJson;
    form.showAdvanced = Boolean(server.env || server.headers || server.tool_timeout || server.toolTimeout || server.enabled_tools || server.enabledTools);
    form.showJsonInput = false;
    settingsState.mcpNotice = copy.value.notices.mcpJsonApplied;
  }

  async function saveMcpServer() {
    const payload = buildMcpServerPayload();
    if (payload === null) {
      return;
    }
    if (!payload.server_id) {
      settingsState.mcpError = copy.value.notices.mcpServerIdRequired;
      return;
    }
    if (payload.type === "stdio" && !payload.command) {
      settingsState.mcpError = copy.value.notices.mcpCommandRequired;
      return;
    }
    if ((payload.type === "sse" || payload.type === "streamableHttp") && !payload.url) {
      settingsState.mcpError = copy.value.notices.mcpUrlRequired;
      return;
    }

    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    try {
      const editingId = settingsState.mcpForm.editingId;
      const response = await requestSettingsJson(editingId ? `/api/settings/mcp/${encodeURIComponent(editingId)}` : "/api/settings/mcp", {
        method: editingId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      settingsState.mcp = normalizeMcpSettings(response);
      settingsState.mcpNotice = response.reload_message || copy.value.notices.mcpSaved;
      resetMcpForm();
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpSaveFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  async function removeMcpServer(server) {
    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    try {
      const response = await requestSettingsJson(`/api/settings/mcp/${encodeURIComponent(server.id)}`, {
        method: "DELETE",
      });
      settingsState.mcp = normalizeMcpSettings(response);
      settingsState.mcpNotice = response.reload_message || copy.value.notices.mcpRemoved;
      if (settingsState.mcpForm.editingId === server.id) {
        resetMcpForm();
      }
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpRemoveFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  async function reloadMcpSettings() {
    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    try {
      const response = await requestSettingsJson("/api/settings/mcp/reload", { method: "POST" });
      settingsState.mcp = normalizeMcpSettings(response);
      settingsState.mcpNotice = response.reload_message || copy.value.notices.mcpReloaded;
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpReloadFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  async function saveScheduleSettings() {
    const defaultTimezone = String(settingsState.scheduleForm.defaultTimezone || "").trim() || "UTC";
    settingsState.scheduleLoading = true;
    settingsState.scheduleError = "";
    settingsState.scheduleNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/schedule", {
        method: "PUT",
        body: JSON.stringify({ default_timezone: defaultTimezone }),
      });
      settingsState.schedule = payload;
      settingsState.scheduleForm.defaultTimezone = payload.default_timezone || defaultTimezone;
      settingsState.scheduleNotice = payload.restart_required
        ? copy.value.notices.scheduleRestartRequired
        : copy.value.notices.scheduleSaved(settingsState.scheduleForm.defaultTimezone);
    } catch (error) {
      settingsState.scheduleError = error?.message || copy.value.notices.scheduleSaveFailed;
    } finally {
      settingsState.scheduleLoading = false;
    }
  }

  function beginCronJobEdit(job) {
    const schedule = job?.schedule || {};
    const payload = job?.payload || {};
    settingsState.cronJobsNotice = "";
    settingsState.cronJobsError = "";
    settingsState.cronJobForm.showEditor = true;
    settingsState.cronJobForm.sessionId = job?.session_id || "";
    settingsState.cronJobForm.jobId = job?.id || "";
    settingsState.cronJobForm.mode = schedule.kind || "cron";
    settingsState.cronJobForm.name = job?.name || "";
    settingsState.cronJobForm.message = payload.message || "";
    settingsState.cronJobForm.everySeconds = schedule.every_ms ? String(Math.max(1, Math.floor(schedule.every_ms / 1000))) : "3600";
    settingsState.cronJobForm.cronExpr = schedule.expr || "0 9 * * *";
    settingsState.cronJobForm.at = schedule.at_ms ? formatDateTimeLocal(schedule.at_ms) : "";
    settingsState.cronJobForm.timezone = schedule.tz || settingsState.schedule.default_timezone || "UTC";
    settingsState.cronJobForm.deliver = payload.deliver !== false;
  }

  function cancelCronJobEdit() {
    resetCronJobForm();
  }

  function beginCronJobCreate() {
    resetCronJobForm();
    settingsState.cronJobsNotice = "";
    settingsState.cronJobsError = "";
    settingsState.cronJobForm.showEditor = true;
  }

  async function saveCronJob() {
    const payload = buildCronJobPayload();
    if (!payload.session_id) {
      settingsState.cronJobsError = copy.value.notices.sessionNotReady;
      return;
    }
    if (!payload.message) {
      settingsState.cronJobsError = copy.value.notices.cronJobMessageRequired;
      return;
    }

    const jobId = settingsState.cronJobForm.jobId;
    settingsState.cronJobsLoading = true;
    settingsState.cronJobsError = "";
    settingsState.cronJobsNotice = "";
    try {
      await requestSettingsJson(jobId ? `/api/cron/jobs/${encodeURIComponent(jobId)}` : "/api/cron/jobs", {
        method: jobId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      settingsState.cronJobsNotice = jobId ? copy.value.notices.cronJobUpdated : copy.value.notices.cronJobCreated;
      resetCronJobForm();
      await loadCronJobs();
    } catch (error) {
      settingsState.cronJobsError = error?.message || copy.value.notices.cronJobSaveFailed;
    } finally {
      settingsState.cronJobsLoading = false;
    }
  }

  async function runCronJobAction(job, action) {
    const sessionId = job?.session_id || getActiveCronSessionId();
    if (!sessionId) {
      settingsState.cronJobsError = copy.value.notices.sessionNotReady;
      return;
    }

    settingsState.cronJobsLoading = true;
    settingsState.cronJobsError = "";
    settingsState.cronJobsNotice = "";
    try {
      if (action === "remove") {
        await requestSettingsJson(`/api/cron/jobs/${encodeURIComponent(job.id)}?session_id=${encodeURIComponent(sessionId)}`, {
          method: "DELETE",
        });
      } else {
        await requestSettingsJson(`/api/cron/jobs/${encodeURIComponent(job.id)}/${encodeURIComponent(action)}`, {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId }),
        });
      }
      settingsState.cronJobsNotice = copy.value.notices.cronJobActionDone;
      await loadCronJobs();
    } catch (error) {
      settingsState.cronJobsError = error?.message || copy.value.notices.cronJobActionFailed;
    } finally {
      settingsState.cronJobsLoading = false;
    }
  }

  function handleSocketMessage(rawData) {
    let payload;
    try {
      payload = JSON.parse(rawData);
    } catch {
      setNotice(copy.value.notices.parseError, "error");
      return;
    }

    if (payload.type === "session") {
      const session = ensureSession(payload.external_chat_id, payload.session_id);
      if (!state.activeExternalChatId) {
        state.activeExternalChatId = session.externalChatId;
      }
      persistActiveSession();
      setNotice(copy.value.notices.liveSessionReady(payload.session_id), "success");
      if (shouldBackfillSessionRuns(session)) {
        void loadCurrentSessionRuns({ force: true });
      }
      return;
    }

    if (payload.type === "message") {
      const externalChatId = payload.external_chat_id || currentSession.value?.externalChatId || generateExternalChatId();
      const session = ensureSession(externalChatId, payload.session_id);
      if (session.channel !== "web") {
        return;
      }
      addMessage(session.externalChatId, makeMessage("assistant", payload.text || "", payload.session_id || "OpenSprite"));
      scrollMessagesToBottom();
      return;
    }

    if (payload.type === "run_event") {
      handleRunEvent(payload);
      if (String(payload.event_type || "").startsWith("permission_")) {
        void loadPermissionRequests();
      }
      scrollMessagesToBottom();
      return;
    }

    if (payload.type === "session_status") {
      applySessionStatus(payload);
      return;
    }

    if (payload.type === "error") {
      setNotice(payload.error || copy.value.notices.gatewayError, "error");
    }
  }

  function connectSocket() {
    const session = ensureActiveWebSession();
    if (!session) {
      return;
    }

    let socketUrl;
    try {
      socketUrl = buildSocketUrl(state.wsUrl, session.externalChatId);
    } catch {
      setNotice(copy.value.notices.invalidWs, "error");
      openSettings("general");
      return;
    }

    if (activeSocket) {
      disconnectSocket(copy.value.notices.refreshConnection, "info");
    }

    state.connectionState = "connecting";
    setNotice(copy.value.notices.connectingTo(state.wsUrl), "info");

    const socket = new WebSocket(socketUrl);
    activeSocket = socket;

    socket.addEventListener("open", () => {
      if (activeSocket !== socket) {
        return;
      }
      state.connectionState = "connected";
      setNotice(copy.value.notices.connected, "success");
    });

    socket.addEventListener("message", (event) => {
      if (activeSocket !== socket) {
        return;
      }
      handleSocketMessage(event.data);
    });

    socket.addEventListener("error", () => {
      if (activeSocket !== socket) {
        return;
      }
      setNotice(copy.value.notices.socketFailed, "error");
    });

    socket.addEventListener("close", () => {
      if (activeSocket !== socket) {
        return;
      }
      const failedToConnect = state.connectionState === "connecting";
      activeSocket = null;
      state.connectionState = "disconnected";
      setNotice(
        failedToConnect ? copy.value.notices.couldNotConnect : copy.value.notices.disconnected,
        failedToConnect ? "error" : "warning",
      );
    });
  }

  function resizeComposer() {
    const input = messageInput.value;
    if (!input) {
      return;
    }
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
  }

  function scrollMessagesToBottom() {
    nextTick(() => {
      const stage = messageStage.value;
      if (stage) {
        stage.scrollTop = stage.scrollHeight;
      }
    });
  }

  function createNewChat() {
    const session = createSession();
    state.sessions.unshift(session);
    state.activeExternalChatId = session.externalChatId;
    writeStoredValue(STORAGE_KEYS.activeExternalChatId, session.externalChatId);
    setNotice(copy.value.notices.newDraft, "info");
    scrollMessagesToBottom();
  }

  function saveConnectionSettings() {
    const nextWsUrl = settingsForm.wsUrl.trim() || DEFAULT_WS_URL;
    const shouldReconnect = state.wsUrl !== nextWsUrl && activeSocket && state.connectionState !== "disconnected";

    state.wsUrl = nextWsUrl;
    state.displayName = settingsForm.displayName.trim() || "Local browser";
    saveRunPanelVisibilitySettings(settingsForm.showRunTimeline, settingsForm.showRunSummary, settingsForm.showRunTrace);

    const requestedExternalChatId = settingsForm.externalChatId.trim();
    if (requestedExternalChatId) {
      ensureSession(requestedExternalChatId);
      state.activeExternalChatId = requestedExternalChatId;
    } else {
      const session = createSession();
      state.sessions.unshift(session);
      state.activeExternalChatId = session.externalChatId;
      settingsForm.externalChatId = session.externalChatId;
    }

    writeStoredValue(STORAGE_KEYS.wsUrl, state.wsUrl);
    writeStoredValue(STORAGE_KEYS.displayName, state.displayName);
    writeStoredValue(STORAGE_KEYS.activeExternalChatId, state.activeExternalChatId);
    settingsForm.wsUrl = state.wsUrl;
    settingsForm.displayName = state.displayName;
    settingsForm.externalChatId = state.activeExternalChatId;

    if (shouldReconnect) {
      connectSocket();
    }
  }

  function toggleSettingsConnection(shouldConnect) {
    if (shouldConnect) {
      saveConnectionSettings();
      connectSocket();
      return;
    }
    disconnectSocket(copy.value.notices.disconnected, "warning");
  }

  async function cancelRun(run) {
    const session = currentSession.value;
    if (!session || !run?.runId || run.status !== "running") {
      return;
    }
    const sessionId = getSessionApiId(session);
    if (!sessionId) {
      setNotice(copy.value.notices.sessionNotReady, "warning");
      return;
    }

    run.cancelPending = true;
    try {
      const response = await fetch(buildRunCancelUrl(state.wsUrl, run.runId, sessionId), { method: "POST" });
      if (!response.ok) {
        throw new Error(`Cancel request failed with HTTP ${response.status}`);
      }
      setNotice(copy.value.notices.cancelRequested(run.runId), "warning");
    } catch (error) {
      setNotice(error?.message || copy.value.notices.cancelFailed, "error");
    } finally {
      run.cancelPending = false;
    }
  }

  async function revertRunFileChange(run, change) {
    const sessionId = run?.sessionId || currentSession.value?.sessionId || "";
    const changeId = change?.changeId || change?.sourceId || "";
    if (!run?.runId || !sessionId || !changeId) {
      setNotice(copy.value.runFileInspector.revertUnavailable, "warning");
      return null;
    }

    try {
      const payload = await requestSettingsJson(buildRunFileChangeRevertPath(run.runId, sessionId, changeId), {
        method: "POST",
        body: JSON.stringify({ dry_run: false }),
      });
      if (!payload?.revert?.applied) {
        setNotice(payload?.revert?.reason || copy.value.runFileInspector.revertUnavailable, "warning");
        return payload?.revert || null;
      }
      setNotice(copy.value.runFileInspector.revertApplied(change.path || ""), "success");
      await loadRunTrace(currentSession.value, run);
      return payload.revert;
    } catch (error) {
      setNotice(error?.message || copy.value.runFileInspector.revertFailed, "error");
      return null;
    }
  }

  async function cleanupWorktreeSandbox(run) {
    const sandbox = run?.worktreeSandbox;
    if (!sandbox?.sandboxPath || !sandbox.cleanupSupported) {
      setNotice(copy.value.notices.worktreeCleanupUnavailable, "warning");
      return null;
    }
    if (typeof window !== "undefined" && !window.confirm(copy.value.runSummary.confirmCleanupSandbox(sandbox.sandboxPath))) {
      return null;
    }

    sandbox.cleanupPending = true;
    try {
      const payload = await requestSettingsJson(buildWorktreeCleanupPath(), {
        method: "POST",
        body: JSON.stringify({ sandbox_path: sandbox.sandboxPath }),
      });
      sandbox.cleanupResult = payload?.cleanup || null;
      if (!payload?.ok) {
        setNotice(payload?.cleanup?.reason || copy.value.notices.worktreeCleanupFailed, "warning");
        return sandbox.cleanupResult;
      }
      sandbox.status = payload.cleanup?.status || "removed";
      sandbox.cleanupSupported = false;
      setNotice(copy.value.notices.worktreeCleanupApplied, "success");
      if (currentSession.value) {
        await loadRunTrace(currentSession.value, run);
      }
      return sandbox.cleanupResult;
    } catch (error) {
      setNotice(error?.message || copy.value.notices.worktreeCleanupFailed, "error");
      return null;
    } finally {
      sandbox.cleanupPending = false;
    }
  }

  function submitMessage(event) {
    event.preventDefault();
    const text = messageText.value.trim();
    if (!text) {
      return;
    }

    if (!activeSocket || activeSocket.readyState !== WebSocket.OPEN) {
      if (state.connectionState === "connecting") {
        setNotice(copy.value.notices.stillConnecting, "info");
        return;
      }
      setNotice(copy.value.notices.inactiveConnection, "warning");
      openSettings("general");
      return;
    }

    const session = currentSession.value;
    if (!session) {
      return;
    }
    if (session.channel !== "web") {
      setNotice(copy.value.composer.readOnlyChannel(session.channel), "info");
      return;
    }

    addMessage(session.externalChatId, makeMessage("user", text, state.displayName || "Local browser"));
    activeSocket.send(
      JSON.stringify({
        external_chat_id: session.externalChatId,
        ...(session.sessionId ? { session_id: session.sessionId } : {}),
        sender_name: state.displayName,
        text,
      }),
    );

    messageText.value = "";
    resizeComposer();
    scrollMessagesToBottom();
  }

  function handleComposerKeydown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      submitMessage(event);
    }
  }

  function applyPrompt(text) {
    messageText.value = text;
    nextTick(() => {
      resizeComposer();
      messageInput.value?.focus();
    });
  }

  async function initializeClient() {
    await loadSessionHistory();
    if (clientDisposed) {
      return;
    }
    void loadPermissionRequests();
    persistActiveSession();
    connectSocket();
  }

  function handleGlobalKeydown(event) {
    const pressedSettingsShortcut = event.key === "," && (event.ctrlKey || event.metaKey);
    if (pressedSettingsShortcut) {
      event.preventDefault();
      openSettings("general");
      return;
    }

    if (event.key === "Escape") {
      closeSettings();
      closeSidebar();
    }
  }

  onMounted(() => {
    addColorSchemeListener();
    applyDocumentPreferences();
    document.addEventListener("keydown", handleGlobalKeydown);
    resizeComposer();
    scrollMessagesToBottom();
    initializeClient();
  });

  onBeforeUnmount(() => {
    clientDisposed = true;
    for (const timer of runSummaryTimers.values()) {
      clearTimeout(timer);
    }
    runSummaryTimers.clear();
    runBackfillTimes.clear();
    removeColorSchemeListener();
    document.removeEventListener("keydown", handleGlobalKeydown);
    document.body.classList.remove("settings-open", "sidebar-open");
    if (activeSocket && activeSocket.readyState !== WebSocket.CLOSED) {
      activeSocket.close(1000, "Client disconnect");
    }
    activeSocket = null;
  });

  return {
    copy,
    prompts,
    state,
    sidebarSessions,
    sessionChannelFilter,
    messageText,
    messageInput,
    messageStage,
    sidebarOpen,
    sidebarCollapsed,
    settingsOpen,
    settingsSection,
    settingsForm,
    settingsState,
    permissionState,
    currentEntries,
    currentMessages,
    currentWorkState,
    currentRuns,
    currentRunsLoading,
    currentRunsError,
    currentRun,
    currentRunTimeline,
    currentRunSummary,
    currentPermissionRequests,
    settingsTitle,
    sessionMeta,
    runtimeHint,
    composerHint,
    connectionLabel,
    connectButtonLabel,
    statusDotClass,
    currentSessionReadOnly,
    sendDisabled,
    setMessageInputRef,
    setMessageStageRef,
    setMessageText,
    getSessionDisplayId,
    getSessionTitle,
    setActiveSession,
    setSessionChannelFilter,
    selectRun,
    selectSettingsSection,
    openSettings,
    closeSettings,
    saveConnectionSettings,
    loadProviderSettings,
    loadModelSettings,
    loadChannelSettings,
    loadScheduleSettings,
    loadMcpSettings,
    loadCronJobs,
    beginChannelConnect,
    cancelChannelConnect,
    saveChannelConnection,
    disconnectChannel,
    beginProviderConnect,
    cancelProviderConnect,
    saveProviderConnection,
    disconnectProvider,
    selectModel,
    beginMcpEdit,
    beginMcpCreate,
    cancelMcpEdit,
    saveMcpServer,
    removeMcpServer,
    reloadMcpSettings,
    toggleMcpAdvanced,
    toggleMcpJsonInput,
    toggleMcpToolGroup,
    applyMcpJson,
    saveScheduleSettings,
    beginCronJobEdit,
    beginCronJobCreate,
    cancelCronJobEdit,
    saveCronJob,
    runCronJobAction,
    toggleSidebar,
    toggleSidebarCollapsed,
    connectSocket,
    resizeComposer,
    createNewChat,
    cancelRun,
    revertRunFileChange,
    cleanupWorktreeSandbox,
    resolvePermissionRequest,
    toggleSettingsConnection,
    submitMessage,
    handleComposerKeydown,
    applyPrompt,
  };
}
