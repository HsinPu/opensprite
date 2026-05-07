import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { getDisplayCopy } from "../i18n/copy";
import { useChannelSettingsActions } from "./useChannelSettingsActions";
import { useDataSettingsActions } from "./useDataSettingsActions";
import { useMcpSettingsActions } from "./useMcpSettingsActions";
import { useModelSettingsActions } from "./useModelSettingsActions";
import { useNetworkSettingsActions } from "./useNetworkSettingsActions";
import { useProviderSettingsActions } from "./useProviderSettingsActions";
import { useScheduleSettingsActions } from "./useScheduleSettingsActions";
import { useUpdateSettingsActions } from "./useUpdateSettingsActions";
import { buildHttpApiUrl, requestSettingsJson as requestSettingsJsonFromApi } from "./settingsApi";
import { createCuratorState, createPermissionState, createSettingsForm, createSettingsState } from "./useSettingsState";

const STORAGE_KEYS = {
  wsUrl: "opensprite:web:wsUrl",
  displayName: "opensprite:web:displayName",
  activeExternalChatId: "opensprite:web:activeExternalChatId",
  showWorkState: "opensprite:web:showWorkState",
  showRunHistory: "opensprite:web:showRunHistory",
  showRunTimeline: "opensprite:web:showRunTimeline",
  showRunSummary: "opensprite:web:showRunSummary",
  showRunTrace: "opensprite:web:showRunTrace",
  language: "opensprite:web:language",
  colorScheme: "opensprite:web:colorScheme",
  sidebarCollapsed: "opensprite:web:sidebarCollapsed",
  overlayProfileId: "opensprite:web:overlayProfileId",
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
const BACKGROUND_PROCESS_LIMIT = 30;
const CURATOR_HISTORY_LIMIT = 5;
const CURATOR_POLL_INTERVAL_MS = 2500;
const TERMINAL_RUN_STATUSES = new Set(["completed", "failed", "cancelled"]);
const TERMINAL_PART_STATES = new Set(["completed", "failed", "cancelled", "error"]);
const CURATOR_BUSY_STATES = new Set(["queued", "running"]);
const RUN_EVENT_KINDS = new Set(["run", "llm", "tool", "verification", "permission", "work", "completion", "file", "process", "text", "system", "other"]);
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
  "subagent.started",
  "subagent.group.started",
  "subagent.group.completed",
  "subagent.group.failed",
  "subagent.group.cancelled",
  "subagent.completed",
  "subagent.failed",
  "subagent.cancelled",
  "workflow.started",
  "workflow.step.started",
  "workflow.step.completed",
  "workflow.step.failed",
  "workflow.completed",
  "workflow.failed",
  "curator.started",
  "curator.completed",
  "curator.failed",
  "auto_continue.scheduled",
  "auto_continue.completed",
  "auto_continue.skipped",
  "background_process.started",
  "background_process.completed",
  "background_process.lost",
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

function generateOverlayProfileId() {
  return `profile-${Date.now().toString(36)}-${randomToken()}`;
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

function makeLiveEntry(message) {
  const role = message?.role === "user" ? "user" : "assistant";
  const createdAt = Number(message?.createdAt || Date.now());
  const text = String(message?.text || "");
  return {
    id: `live-entry-${createdAt.toString(36)}-${randomToken()}`,
    type: role,
    role,
    runId: "",
    status: "",
    text,
    content: [],
    meta: message?.meta || (role === "user" ? "You" : "OpenSprite"),
    createdAt,
    updatedAt: createdAt,
    metadata: {},
  };
}

function coerceStringList(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function normalizeCommandCatalog(payload) {
  const commands = Array.isArray(payload?.commands) ? payload.commands : [];
  return commands.map((item) => {
    const name = String(item?.name || "").trim();
    const command = String(item?.command || (name ? `/${name}` : "")).trim();
    if (!name || !command.startsWith("/")) {
      return null;
    }
    return {
      name,
      command,
      usage: String(item?.usage || command).trim() || command,
      description: String(item?.description || "").trim(),
      category: String(item?.category || "").trim(),
      subcommands: coerceStringList(item?.subcommands),
    };
  }).filter(Boolean);
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
  if (normalized.startsWith("background_process.")) {
    return "process";
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
  if (normalized === "background_process.started") {
    return "running";
  }
  if (normalized === "background_process.lost") {
    return "lost";
  }
  if (normalized === "background_process.completed") {
    return Number(payload.exit_code ?? payload.exitCode ?? 0) === 0 ? "completed" : "failed";
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

function normalizeBackgroundProcessArtifact(eventType, payload, fallback = {}) {
  if (!String(eventType || "").startsWith("background_process.")) {
    return null;
  }
  const processSessionId = String(payload.process_session_id || payload.processSessionId || fallback.sourceId || "").trim();
  const command = String(payload.command || "").trim();
  if (!processSessionId && !command) {
    return null;
  }
  const normalizedEventType = String(eventType || "").trim();
  const state = normalizedEventType === "background_process.started"
    ? "running"
    : normalizedEventType === "background_process.lost"
      ? "lost"
      : normalizedEventType === "background_process.completed"
        ? (Number(payload.exit_code ?? payload.exitCode ?? 0) === 0 ? "completed" : "failed")
        : String(payload.state || fallback.status || inferRunEventStatus(eventType, payload)).trim() || "completed";
  const title = command ? previewText(command) : processSessionId;
  const exitCode = payload.exit_code ?? payload.exitCode;
  const termination = String(payload.termination_reason || payload.terminationReason || "").trim();
  const detailParts = [];
  if (processSessionId) {
    detailParts.push(processSessionId);
  }
  if (termination) {
    detailParts.push(termination);
  }
  if (exitCode !== null && exitCode !== undefined) {
    detailParts.push(`exit ${exitCode}`);
  }
  return {
    artifactId: `process:${processSessionId || fallback.sourceId || fallback.createdAt}`,
    artifactType: "background_process",
    kind: "process",
    status: state,
    phase: normalizedEventType.replace("background_process.", ""),
    title,
    detail: detailParts.join(" · "),
    source: "event",
    sourceId: processSessionId || String(fallback.sourceId || ""),
    createdAt: normalizeEventTimestamp(fallback.createdAt),
    toolName: "",
    toolCallId: "",
    iteration: "",
    path: "",
    action: "",
    diffLen: 0,
    diffPreview: "",
    snapshotsAvailable: { before: false, after: false },
    metadata: {
      process_session_id: processSessionId,
      command,
      cwd: String(payload.cwd || "").trim(),
      pid: payload.pid ?? null,
      state,
      termination_reason: termination,
      exit_code: exitCode ?? null,
      notify_mode: String(payload.notify_mode || payload.notifyMode || "").trim(),
      output_tail: String(payload.output_tail || payload.outputTail || "").trim(),
    },
  };
}

function normalizeTraceEventArtifact(eventType, payload, artifact, fallback = {}) {
  return normalizeRunArtifact(artifact, fallback)
    || normalizeBackgroundProcessArtifact(eventType, payload, fallback);
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

function normalizeDelegatedTask(payload) {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const taskId = String(payload.task_id || payload.taskId || "").trim();
  if (!taskId) {
    return null;
  }
  return {
    taskId,
    promptType: String(payload.prompt_type || payload.promptType || "").trim() || null,
    status: String(payload.status || "unknown").trim() || "unknown",
    selected: coerceBoolean(payload.selected),
    summary: String(payload.summary || "").trim(),
    error: String(payload.error || "").trim(),
    childSessionId: String(payload.child_session_id || payload.childSessionId || "").trim() || null,
    lastChildRunId: String(payload.last_child_run_id || payload.lastChildRunId || "").trim() || null,
    metadata: payload.metadata && typeof payload.metadata === "object" ? payload.metadata : {},
    createdAt: normalizeEventTimestamp(payload.created_at ?? payload.createdAt),
    updatedAt: normalizeEventTimestamp(payload.updated_at ?? payload.updatedAt),
  };
}

function normalizeWorkState(payload) {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const objective = String(payload.objective || "").trim();
  if (!objective) {
    return null;
  }
  const delegatedTasks = Array.isArray(payload.delegated_tasks || payload.delegatedTasks)
    ? (payload.delegated_tasks || payload.delegatedTasks).map(normalizeDelegatedTask).filter(Boolean)
    : [];
  const selectedDelegatedTask = delegatedTasks.find((task) => task.selected) || null;
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
    followUpWorkflow: String(payload.follow_up_workflow || payload.followUpWorkflow || "").trim() || null,
    followUpStepId: String(payload.follow_up_step_id || payload.followUpStepId || "").trim() || null,
    followUpStepLabel: String(payload.follow_up_step_label || payload.followUpStepLabel || "").trim() || null,
    followUpPromptType: String(payload.follow_up_prompt_type || payload.followUpPromptType || "").trim() || null,
    verificationAction: String(payload.verification_action || payload.verificationAction || "").trim() || null,
    verificationPath: String(payload.verification_path || payload.verificationPath || "").trim() || null,
    verificationPytestArgs: coerceStringList(payload.verification_pytest_args || payload.verificationPytestArgs),
    activeTaskDetail: String(payload.active_task_detail || payload.activeTaskDetail || "").trim(),
    lastNextAction: String(payload.last_next_action || payload.lastNextAction || "").trim(),
    delegatedTasks,
    activeDelegateTaskId: String(payload.active_delegate_task_id || payload.activeDelegateTaskId || "").trim() || selectedDelegatedTask?.taskId || null,
    activeDelegatePromptType: String(payload.active_delegate_prompt_type || payload.activeDelegatePromptType || "").trim() || selectedDelegatedTask?.promptType || null,
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

  function applyCompletionGateEvent(session, payload, createdAt) {
    if (!session?.workState) {
      return;
    }
    mergeSessionWorkState(session, {
      followUpWorkflow: payload.follow_up_workflow || payload.followUpWorkflow || session.workState.followUpWorkflow,
      followUpStepId: payload.follow_up_step_id || payload.followUpStepId || session.workState.followUpStepId,
      followUpStepLabel: payload.follow_up_step_label || payload.followUpStepLabel || session.workState.followUpStepLabel,
      followUpPromptType: payload.follow_up_prompt_type || payload.followUpPromptType || session.workState.followUpPromptType,
      verificationAction: payload.verification_action || payload.verificationAction || session.workState.verificationAction,
      verificationPath: payload.verification_path || payload.verificationPath || session.workState.verificationPath,
      verificationPytestArgs: payload.verification_pytest_args || payload.verificationPytestArgs || session.workState.verificationPytestArgs,
      activeTaskDetail: payload.active_task_detail || payload.activeTaskDetail || session.workState.activeTaskDetail,
      updatedAt: createdAt,
    });
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

function isCuratorBusy(status) {
  if (!status || typeof status !== "object") {
    return false;
  }
  const state = String(status.state || "").trim();
  return Boolean(status.running || status.queued || status.rerun_pending || CURATOR_BUSY_STATES.has(state));
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

function buildCuratorStatusPath(sessionId) {
  return `/api/curator/status?session_id=${encodeURIComponent(sessionId)}`;
}

function buildCuratorHistoryPath(sessionId, limit = CURATOR_HISTORY_LIMIT) {
  return `/api/curator/history?session_id=${encodeURIComponent(sessionId)}&limit=${encodeURIComponent(limit)}`;
}

function buildCuratorActionPath(action, sessionId, scope = "") {
  const params = new URLSearchParams({ session_id: sessionId });
  if (scope) {
    params.set("scope", scope);
  }
  return `/api/curator/${encodeURIComponent(action)}?${params.toString()}`;
}

function buildRunsPath(sessionId) {
  return `/api/runs?session_id=${encodeURIComponent(sessionId)}&limit=${RUN_HISTORY_LIMIT}`;
}

function buildBackgroundProcessesPath(sessionId = "", limit = BACKGROUND_PROCESS_LIMIT) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (sessionId) {
    params.set("session_id", sessionId);
  }
  return `/api/background-processes?${params.toString()}`;
}

function normalizeBackgroundProcess(payload) {
  const processSessionId = String(payload?.process_session_id || payload?.processSessionId || "").trim();
  if (!processSessionId) {
    return null;
  }
  const ownerSessionId = String(payload?.owner_session_id || payload?.ownerSessionId || "").trim();
  const ownerChannel = String(payload?.owner_channel || payload?.ownerChannel || channelFromSessionId(ownerSessionId) || "").trim();
  const ownerExternalChatId = String(payload?.owner_external_chat_id || payload?.ownerExternalChatId || "").trim()
    || externalChatIdFromSessionId(ownerSessionId);
  const finishedAt = payload?.finished_at ?? payload?.finishedAt;
  const exitCode = payload?.exit_code ?? payload?.exitCode;
  return {
    processSessionId,
    ownerSessionId,
    ownerRunId: String(payload?.owner_run_id || payload?.ownerRunId || "").trim(),
    ownerChannel,
    ownerExternalChatId,
    pid: payload?.pid ?? null,
    command: String(payload?.command || "").trim(),
    cwd: String(payload?.cwd || "").trim(),
    state: String(payload?.state || "unknown").trim() || "unknown",
    terminationReason: String(payload?.termination_reason || payload?.terminationReason || "").trim(),
    exitCode: Number.isFinite(Number(exitCode)) ? Number(exitCode) : null,
    notifyMode: String(payload?.notify_mode || payload?.notifyMode || "").trim(),
    outputTail: String(payload?.output_tail || payload?.outputTail || "").trim(),
    outputPath: String(payload?.output_path || payload?.outputPath || "").trim(),
    metadata: payload?.metadata && typeof payload.metadata === "object" ? payload.metadata : {},
    startedAt: normalizeEventTimestamp(payload?.started_at ?? payload?.startedAt),
    updatedAt: normalizeEventTimestamp(payload?.updated_at ?? payload?.updatedAt),
    finishedAt: finishedAt ? normalizeEventTimestamp(finishedAt) : null,
  };
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

function formatSubagentDetail(payload) {
  return [payload.prompt_type || payload.promptType, payload.task_id || payload.taskId].filter(Boolean).join(" · ");
}

function formatSubagentGroupDetail(payload) {
  const summary = String(payload.summary || payload.message || payload.error || "").trim();
  if (summary) {
    return summary;
  }
  const total = coerceNonNegativeInteger(payload.total_tasks ?? payload.totalTasks);
  return total > 0 ? `${total} task(s)` : "";
}

function formatWorkflowDetail(payload) {
  return String(payload.summary || payload.error || payload.task_preview || payload.message || payload.workflow || "").trim();
}

function formatWorkflowStepDetail(payload) {
  return String(payload.summary || payload.error || payload.task_preview || payload.label || "").trim();
}

function formatAutoContinueDetail(payload) {
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

function normalizeRunSummary(payload) {
  if (!payload || typeof payload !== "object") {
    return null;
  }

  const verification = payload.verification && typeof payload.verification === "object" ? payload.verification : {};
  const review = payload.review && typeof payload.review === "object" ? payload.review : {};
  const counts = payload.counts && typeof payload.counts === "object" ? payload.counts : {};
  const artifactCounts = payload.artifact_counts && typeof payload.artifact_counts === "object" ? payload.artifact_counts : {};
  const parallelDelegation = normalizeParallelDelegationSummary(payload.parallel_delegation || payload.parallelDelegation);
  const structuredSubagents = normalizeStructuredSubagentsSummary(payload.structured_subagents || payload.structuredSubagents);
  const workflows = normalizeWorkflowSummary(payload.workflows);
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
    review: {
      required: coerceBoolean(review.required),
      attempted: coerceBoolean(review.attempted),
      passed: coerceBoolean(review.passed),
      status: String(review.status || "not_required").trim() || "not_required",
      summary: String(review.summary || "").trim(),
      promptTypes: coerceStringList(review.prompt_types || review.promptTypes),
      findingCount: coerceNonNegativeInteger(review.finding_count ?? review.findingCount),
    },
    parallelDelegation,
    structuredSubagents,
    workflows,
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

function normalizeWorkflowSummary(payload) {
  if (!payload || typeof payload !== "object") {
    return { total: 0, byWorkflow: {}, byStatus: {}, results: [] };
  }
  const byWorkflow = payload.by_workflow && typeof payload.by_workflow === "object" ? payload.by_workflow : {};
  const byStatus = payload.by_status && typeof payload.by_status === "object" ? payload.by_status : {};
  const results = Array.isArray(payload.results)
    ? payload.results
        .map((item) => {
          if (!item || typeof item !== "object") {
            return null;
          }
          return {
            workflowRunId: String(item.workflow_run_id || item.workflowRunId || "").trim() || null,
            workflow: String(item.workflow || "").trim() || null,
            status: String(item.status || "unknown").trim() || "unknown",
            taskPreview: String(item.task_preview || item.taskPreview || "").trim(),
            totalSteps: coerceNonNegativeInteger(item.total_steps ?? item.totalSteps),
            completedSteps: coerceNonNegativeInteger(item.completed_steps ?? item.completedSteps),
            failedSteps: coerceNonNegativeInteger(item.failed_steps ?? item.failedSteps),
            summary: String(item.summary || "").trim(),
            createdAt: normalizeEventTimestamp(item.created_at ?? item.createdAt),
          };
        })
        .filter(Boolean)
    : [];
  return {
    total: coerceNonNegativeInteger(payload.total),
    byWorkflow: Object.fromEntries(Object.entries(byWorkflow).map(([key, value]) => [String(key || "").trim(), coerceNonNegativeInteger(value)]).filter(([key]) => key)),
    byStatus: Object.fromEntries(Object.entries(byStatus).map(([key, value]) => [String(key || "").trim(), coerceNonNegativeInteger(value)]).filter(([key]) => key)),
    results,
  };
}

function normalizeStructuredSubagentsSummary(payload) {
  if (!payload || typeof payload !== "object") {
    return {
      total: 0,
      byPromptType: {},
      byStatus: {},
      totalSections: 0,
      totalItems: 0,
      totalFindings: 0,
      totalQuestions: 0,
      totalResidualRisks: 0,
      results: [],
    };
  }
  const byPromptType = payload.by_prompt_type && typeof payload.by_prompt_type === "object" ? payload.by_prompt_type : {};
  const byStatus = payload.by_status && typeof payload.by_status === "object" ? payload.by_status : {};
  const results = Array.isArray(payload.results)
    ? payload.results
        .map((item) => {
          if (!item || typeof item !== "object") {
            return null;
          }
          return {
            taskId: String(item.task_id || item.taskId || "").trim() || null,
            promptType: String(item.prompt_type || item.promptType || "").trim() || null,
            status: String(item.status || "inconclusive").trim() || "inconclusive",
            summary: String(item.summary || "").trim(),
            sectionCount: coerceNonNegativeInteger(item.section_count ?? item.sectionCount),
            itemCount: coerceNonNegativeInteger(item.item_count ?? item.itemCount),
            findingCount: coerceNonNegativeInteger(item.finding_count ?? item.findingCount),
            questionCount: coerceNonNegativeInteger(item.question_count ?? item.questionCount),
            residualRiskCount: coerceNonNegativeInteger(item.residual_risk_count ?? item.residualRiskCount),
            createdAt: normalizeEventTimestamp(item.created_at ?? item.createdAt),
          };
        })
        .filter(Boolean)
    : [];
  return {
    total: coerceNonNegativeInteger(payload.total),
    byPromptType: Object.fromEntries(Object.entries(byPromptType).map(([key, value]) => [String(key || "").trim(), coerceNonNegativeInteger(value)]).filter(([key]) => key)),
    byStatus: Object.fromEntries(Object.entries(byStatus).map(([key, value]) => [String(key || "").trim(), coerceNonNegativeInteger(value)]).filter(([key]) => key)),
    totalSections: coerceNonNegativeInteger(payload.total_sections ?? payload.totalSections),
    totalItems: coerceNonNegativeInteger(payload.total_items ?? payload.totalItems),
    totalFindings: coerceNonNegativeInteger(payload.total_findings ?? payload.totalFindings),
    totalQuestions: coerceNonNegativeInteger(payload.total_questions ?? payload.totalQuestions),
    totalResidualRisks: coerceNonNegativeInteger(payload.total_residual_risks ?? payload.totalResidualRisks),
    results,
  };
}

function normalizeParallelDelegationSummary(payload) {
  if (!payload || typeof payload !== "object") {
    return { groupCount: 0, taskCount: 0, groups: [] };
  }
  const groups = Array.isArray(payload.groups)
    ? payload.groups
        .map((group) => {
          if (!group || typeof group !== "object") {
            return null;
          }
          const groupId = String(group.group_id || group.groupId || "").trim();
          if (!groupId) {
            return null;
          }
          const tasks = Array.isArray(group.tasks)
            ? group.tasks
                .map((task) => {
                  if (!task || typeof task !== "object") {
                    return null;
                  }
                  return {
                    taskId: String(task.task_id || task.taskId || "").trim() || null,
                    promptType: String(task.prompt_type || task.promptType || "").trim() || null,
                    status: String(task.status || "unknown").trim() || "unknown",
                    summary: String(task.summary || "").trim(),
                    error: String(task.error || "").trim(),
                    childSessionId: String(task.child_session_id || task.childSessionId || "").trim() || null,
                    childRunId: String(task.child_run_id || task.childRunId || "").trim() || null,
                    fanoutIndex: coerceNonNegativeInteger(task.fanout_index ?? task.fanoutIndex),
                  };
                })
                .filter(Boolean)
            : [];
          return {
            groupId,
            status: String(group.status || "unknown").trim() || "unknown",
            totalTasks: coerceNonNegativeInteger(group.total_tasks ?? group.totalTasks),
            maxParallel: coerceNonNegativeInteger(group.max_parallel ?? group.maxParallel),
            completedCount: coerceNonNegativeInteger(group.completed_count ?? group.completedCount),
            failedCount: coerceNonNegativeInteger(group.failed_count ?? group.failedCount),
            cancelledCount: coerceNonNegativeInteger(group.cancelled_count ?? group.cancelledCount),
            summary: String(group.summary || "").trim(),
            createdAt: normalizeEventTimestamp(group.created_at ?? group.createdAt),
            tasks,
          };
        })
        .filter(Boolean)
    : [];
  return {
    groupCount: coerceNonNegativeInteger(payload.group_count ?? payload.groupCount ?? groups.length),
    taskCount: coerceNonNegativeInteger(payload.task_count ?? payload.taskCount ?? groups.reduce((total, group) => total + (group.totalTasks || group.tasks.length), 0)),
    groups,
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

  if (eventType === "subagent.group.started") {
    return {
      label: copy.run.parallelDelegationStarted,
      detail: formatSubagentGroupDetail(payload),
      tone: "running",
    };
  }

  if (eventType === "subagent.group.completed") {
    return {
      label: copy.run.parallelDelegationCompleted,
      detail: formatSubagentGroupDetail(payload),
      tone: "success",
    };
  }

  if (eventType === "subagent.group.failed") {
    return {
      label: copy.run.parallelDelegationFailed,
      detail: formatSubagentGroupDetail(payload),
      tone: "error",
    };
  }

  if (eventType === "subagent.group.cancelled") {
    return {
      label: copy.run.parallelDelegationCancelled,
      detail: formatSubagentGroupDetail(payload),
      tone: "warning",
    };
  }

  if (eventType === "subagent.started") {
    return {
      label: copy.run.subagentStarted,
      detail: payload.message || formatSubagentDetail(payload),
      tone: "running",
    };
  }

  if (eventType === "subagent.completed") {
    return {
      label: copy.run.subagentCompleted,
      detail: payload.summary || formatSubagentDetail(payload),
      tone: "success",
    };
  }

  if (eventType === "subagent.failed") {
    return {
      label: copy.run.subagentFailed,
      detail: payload.error || formatSubagentDetail(payload),
      tone: "error",
    };
  }

  if (eventType === "subagent.cancelled") {
    return {
      label: copy.run.cancelled,
      detail: payload.error || formatSubagentDetail(payload),
      tone: "warning",
    };
  }

  if (eventType === "workflow.started") {
    return {
      label: copy.run.workflowStarted,
      detail: formatWorkflowDetail(payload),
      tone: "running",
    };
  }

  if (eventType === "workflow.step.started") {
    return {
      label: copy.run.workflowStepStarted,
      detail: formatWorkflowStepDetail(payload),
      tone: "running",
    };
  }

  if (eventType === "workflow.step.completed") {
    return {
      label: copy.run.workflowStepCompleted,
      detail: formatWorkflowStepDetail(payload),
      tone: "success",
    };
  }

  if (eventType === "workflow.step.failed") {
    return {
      label: copy.run.workflowStepFailed,
      detail: formatWorkflowStepDetail(payload),
      tone: "error",
    };
  }

  if (eventType === "workflow.completed") {
    return {
      label: copy.run.workflowCompleted,
      detail: formatWorkflowDetail(payload),
      tone: "success",
    };
  }

  if (eventType === "workflow.failed") {
    return {
      label: copy.run.workflowFailed,
      detail: formatWorkflowDetail(payload),
      tone: "error",
    };
  }

  if (eventType === "curator.started") {
    return {
      label: copy.run.curatorStarted,
      detail: payload.message || payload.summary || "",
      tone: "running",
    };
  }

  if (eventType === "curator.completed") {
    return {
      label: copy.run.curatorCompleted,
      detail: payload.summary || payload.message || "",
      tone: "success",
    };
  }

  if (eventType === "curator.failed") {
    return {
      label: copy.run.curatorFailed,
      detail: payload.error || payload.message || "",
      tone: "error",
    };
  }

  if (eventType === "auto_continue.scheduled") {
    return {
      label: copy.run.autoContinueScheduled,
      detail: formatAutoContinueDetail(payload),
      tone: "running",
    };
  }

  if (eventType === "auto_continue.completed") {
    return {
      label: copy.run.autoContinueCompleted,
      detail: formatAutoContinueDetail(payload),
      tone: "success",
    };
  }

  if (eventType === "auto_continue.skipped") {
    return {
      label: copy.run.autoContinueSkipped,
      detail: formatAutoContinueDetail(payload),
      tone: "warning",
    };
  }

  if (eventType === "background_process.started") {
    return {
      label: copy.run.backgroundProcessStarted || "Background process started",
      detail: payload.command || payload.process_session_id || "",
      tone: "running",
    };
  }

  if (eventType === "background_process.completed") {
    const exitCode = payload.exit_code ?? payload.exitCode;
    return {
      label: Number(exitCode ?? 0) === 0
        ? (copy.run.backgroundProcessCompleted || "Background process completed")
        : (copy.run.backgroundProcessFailed || "Background process failed"),
      detail: [payload.command, exitCode !== undefined && exitCode !== null ? `exit ${exitCode}` : ""].filter(Boolean).join(" · "),
      tone: Number(exitCode ?? 0) === 0 ? "success" : "error",
    };
  }

  if (eventType === "background_process.lost") {
    return {
      label: copy.run.backgroundProcessLost || "Background process lost",
      detail: payload.command || payload.process_session_id || "runtime restart",
      tone: "warning",
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
  const storedOverlayProfileId = readStoredValue(STORAGE_KEYS.overlayProfileId, "");
  const initialLanguage = readStoredChoice(STORAGE_KEYS.language, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES);
  const initialColorScheme = readStoredChoice(STORAGE_KEYS.colorScheme, DEFAULT_COLOR_SCHEME, SUPPORTED_COLOR_SCHEMES);
  const initialCopy = getDisplayCopy(initialLanguage);
  const initialSession = createSession(
    isExternalChannelSessionId(storedExternalChatId) ? generateExternalChatId() : storedExternalChatId || generateExternalChatId(),
  );

  const state = reactive({
    wsUrl: readStoredValue(STORAGE_KEYS.wsUrl, DEFAULT_WS_URL),
    displayName: readStoredValue(STORAGE_KEYS.displayName, "Local browser"),
    showWorkState: readStoredBoolean(STORAGE_KEYS.showWorkState, true),
    showRunHistory: readStoredBoolean(STORAGE_KEYS.showRunHistory, true),
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
    commandCatalog: {
      commands: [],
      loading: false,
      error: "",
    },
    backgroundProcesses: {
      processes: [],
      counts: {},
      loading: false,
      error: "",
      lastLoadedAt: null,
    },
  });

  const overlayProfileId = ref(storedOverlayProfileId || generateOverlayProfileId());
  writeStoredValue(STORAGE_KEYS.overlayProfileId, overlayProfileId.value);

  const copy = computed(() => getDisplayCopy(state.language));
  const prompts = computed(() => copy.value.prompts);

  const messageText = ref("");
  const messageInput = ref(null);
  const messageStage = ref(null);
  const toasts = ref([]);
  const sidebarOpen = ref(false);
  const sidebarCollapsed = ref(readStoredBoolean(STORAGE_KEYS.sidebarCollapsed, false));
  const sessionChannelFilter = ref("all");
  const settingsOpen = ref(false);
  const settingsSection = ref("general");
  const settingsForm = reactive(createSettingsForm(state));
  const settingsState = reactive(createSettingsState());
  const permissionState = reactive(createPermissionState());
  const curatorState = reactive(createCuratorState());

  let activeSocket = null;
  let colorSchemeMediaQuery = null;
  let clientDisposed = false;
  const runSummaryTimers = new Map();
  const runBackfillTimes = new Map();
  let curatorPollTimer = null;
  let codexAuthPollTimer = null;
  let copilotAuthPollTimer = null;
  let curatorPollSessionId = "";
  let toastId = 0;
  const toastTimers = new Map();
  let curatorActionToken = "";

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

  const currentCuratorStatus = computed(() => curatorState.status || null);

  const currentSessionApiId = computed(() => getCuratorSessionId(currentSession.value));

  const activeBackgroundProcesses = computed(() => {
    const sessionId = currentSessionApiId.value;
    if (!sessionId) {
      return [];
    }
    return state.backgroundProcesses.processes.filter((process) => process.ownerSessionId === sessionId);
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

  const commandHints = computed(() => {
    const raw = messageText.value.trimStart();
    if (!raw.startsWith("/")) {
      return [];
    }
    const token = raw.split(/\s+/, 1)[0];
    if (raw.length > token.length) {
      return [];
    }
    const query = token.toLowerCase();
    if (query.includes("@")) {
      return [];
    }
    const commands = state.commandCatalog.commands || [];
    return commands
      .filter((command) => command.command.toLowerCase().startsWith(query))
      .slice(0, 6);
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
    if (element) {
      scrollMessagesToBottom();
    }
  }

  function setMessageText(value) {
    messageText.value = value;
  }

  function saveRunPanelVisibilitySettings(showWorkState, showRunHistory, showRunTimeline, showRunSummary, showRunTrace) {
    state.showWorkState = Boolean(showWorkState);
    state.showRunHistory = Boolean(showRunHistory);
    state.showRunTimeline = Boolean(showRunTimeline);
    state.showRunSummary = Boolean(showRunSummary);
    state.showRunTrace = Boolean(showRunTrace);
    writeStoredValue(STORAGE_KEYS.showWorkState, String(state.showWorkState));
    writeStoredValue(STORAGE_KEYS.showRunHistory, String(state.showRunHistory));
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
      clearCuratorPollTimer();
      curatorActionToken = "";
      curatorState.action = "";
      curatorState.status = null;
      curatorState.error = "";
      curatorState.history = [];
      curatorState.historyLoading = false;
      curatorState.historyError = "";
      void loadCurrentSessionRuns();
      void refreshCuratorState();
      void loadBackgroundProcesses({ quiet: true });
      scrollMessagesToBottom();
    },
    { immediate: true },
  );

  watch(
    () => [currentEntries.value.length, currentMessages.value.length],
    () => {
      scrollMessagesToBottom();
    },
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
    () => [
      settingsForm.showWorkState,
      settingsForm.showRunHistory,
      settingsForm.showRunTimeline,
      settingsForm.showRunSummary,
      settingsForm.showRunTrace,
    ],
    ([showWorkState, showRunHistory, showRunTimeline, showRunSummary, showRunTrace]) => {
      saveRunPanelVisibilitySettings(showWorkState, showRunHistory, showRunTimeline, showRunSummary, showRunTrace);
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

  function getCuratorSessionId(session) {
    if (!session) {
      return "";
    }
    if (session.sessionId) {
      return session.sessionId;
    }
    if (session.channel && session.channel !== "web") {
      return "";
    }
    return session.externalChatId ? `web:${session.externalChatId}` : "";
  }

  function isCurrentCuratorSessionId(sessionId) {
    return Boolean(sessionId) && getCuratorSessionId(currentSession.value) === sessionId;
  }

  function clearCuratorPollTimer() {
    if (curatorPollTimer) {
      clearTimeout(curatorPollTimer);
    }
    curatorPollTimer = null;
    curatorPollSessionId = "";
  }

  function scheduleCuratorPoll(status = curatorState.status, sessionId = getCuratorSessionId(currentSession.value)) {
    clearCuratorPollTimer();
    if (clientDisposed || !sessionId || !isCuratorBusy(status)) {
      return;
    }
    curatorPollSessionId = sessionId;
    curatorPollTimer = setTimeout(() => {
      curatorPollTimer = null;
      if (clientDisposed || curatorPollSessionId !== sessionId || !isCurrentCuratorSessionId(sessionId)) {
        curatorPollSessionId = "";
        return;
      }
      void loadCuratorStatus({ sessionId, quiet: true });
    }, CURATOR_POLL_INTERVAL_MS);
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

  function viewExternalChatIdForPayload(payload) {
    const sessionId = String(payload?.session_id || payload?.sessionId || "").trim();
    const channel = String(payload?.channel || channelFromSessionId(sessionId) || "web").trim() || "web";
    const transportExternalChatId = String(payload?.external_chat_id || payload?.externalChatId || "").trim()
      || externalChatIdFromSessionId(sessionId)
      || generateExternalChatId();
    return channel === "web" ? transportExternalChatId : (sessionId || `${channel}:${transportExternalChatId}`);
  }

  function addMessage(externalChatId, message) {
    const session = ensureSession(externalChatId);
    session.messages.push(message);
    if (session.entries.length) {
      session.entries.push(makeLiveEntry(message));
    }
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
      return;
    }
    if (eventType === "completion_gate.evaluated") {
      applyCompletionGateEvent(session, payload, createdAt);
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
    const externalChatId = viewExternalChatIdForPayload(payload);
    const session = ensureSession(externalChatId, payload.session_id);
    const runId = String(payload.run_id || `run-${Date.now().toString(36)}-${randomToken()}`);
    const eventType = String(payload.event_type || "run_event");
    const eventPayload = coerceEventPayload(payload.payload);
    const eventKind = normalizeRunKind(payload.kind, inferRunEventKind(eventType));
    const eventStatus = String(payload.status || inferRunEventStatus(eventType, eventPayload)).trim();
    const createdAt = normalizeEventTimestamp(payload.created_at);
    const eventArtifact = normalizeTraceEventArtifact(eventType, eventPayload, payload.artifact, {
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
    if (eventType.startsWith("curator.")) {
      const curatorSessionId = getCuratorSessionId(session);
      if (curatorSessionId && isCurrentCuratorSessionId(curatorSessionId)) {
        void refreshCuratorState({ sessionId: curatorSessionId, quiet: true });
      }
    }
    if (eventType.startsWith("background_process.")) {
      void loadBackgroundProcesses({ quiet: true });
    }
  }

  function setNotice(text, tone) {
    state.notice.text = text;
    state.notice.tone = tone;
  }

  function showToast(text, tone = "info") {
    const normalized = String(text || "").trim();
    if (!normalized) {
      return;
    }
    const id = `toast-${Date.now()}-${toastId += 1}`;
    toasts.value = [...toasts.value, { id, text: normalized, tone }].slice(-4);
    const timer = window.setTimeout(() => dismissToast(id), 4500);
    toastTimers.set(id, timer);
  }

  function dismissToast(id) {
    const timer = toastTimers.get(id);
    if (timer) {
      clearTimeout(timer);
      toastTimers.delete(id);
    }
    toasts.value = toasts.value.filter((toast) => toast.id !== id);
  }

  function setSettingsSuccess(noticeKey, text) {
    settingsState[noticeKey] = text;
    showToast(text, "success");
  }

  const {
    loadChannelSettings,
    beginChannelConnect,
    cancelChannelConnect,
    saveChannelConnection,
    disconnectChannel,
  } = useChannelSettingsActions({
    settingsState,
    requestSettingsJson,
    copy,
    setSettingsSuccess,
    cancelProviderConnect,
  });

  const {
    loadModelSettings,
    selectModel,
    applyOpenRouterRecommendedOptions,
    saveOpenRouterOptions,
    saveLlmSettings,
    saveMediaModel,
  } = useModelSettingsActions({
    settingsState,
    requestSettingsJson,
    copy,
    setSettingsSuccess,
    loadProviderSettings: () => loadProviderSettings(),
  });

  const {
    loadMcpSettings,
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
  } = useMcpSettingsActions({
    settingsState,
    requestSettingsJson,
    copy,
    setSettingsSuccess,
  });

  const { loadDataSettings, loadDataSessionTimeline } = useDataSettingsActions({
    settingsState,
    requestSettingsJson,
    copy,
  });

  const { loadNetworkSettings, saveNetworkSettings } = useNetworkSettingsActions({
    settingsState,
    requestSettingsJson,
    copy,
    setSettingsSuccess,
  });

  const { loadScheduleSettings, saveScheduleSettings } = useScheduleSettingsActions({
    settingsState,
    requestSettingsJson,
    copy,
    setSettingsSuccess,
  });

  const {
    loadProviderSettings,
    loadCodexAuthStatus,
    loadCopilotAuthStatus,
    beginProviderConnect,
    saveProviderConnection,
    disconnectProvider,
    setProviderCredential,
    deleteCredential,
    connectCodexProvider,
    connectOAuthProvider,
    connectCopilotProvider,
  } = useProviderSettingsActions({
    settingsState,
    requestSettingsJson,
    copy,
    setSettingsSuccess,
    cancelChannelConnect,
    cancelProviderConnect,
    loadModelSettings,
    startCodexAuthLogin,
    startCopilotAuthLogin,
  });

  const { loadUpdateStatus, runUpdate } = useUpdateSettingsActions({
    settingsState,
    requestSettingsJson,
    copy,
  });

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

  async function selectBackgroundProcess(process) {
    const ownerSessionId = String(process?.ownerSessionId || "").trim();
    const ownerChannel = String(process?.ownerChannel || channelFromSessionId(ownerSessionId) || "web").trim() || "web";
    const ownerExternalChatId = String(process?.ownerExternalChatId || "").trim() || externalChatIdFromSessionId(ownerSessionId);
    const externalChatId = ownerChannel === "web" ? ownerExternalChatId : ownerSessionId;
    if (!externalChatId) {
      return;
    }

    const session = ensureSession(externalChatId, ownerSessionId);
    session.channel = ownerChannel;
    setActiveSession(session.externalChatId);
    await loadCurrentSessionRuns({ force: true });
    if (process?.ownerRunId) {
      selectRun(process.ownerRunId);
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
    settingsForm.showWorkState = state.showWorkState;
    settingsForm.showRunHistory = state.showRunHistory;
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
    return requestSettingsJsonFromApi(state.wsUrl, pathname, options);
  }

  async function loadCommandCatalog() {
    state.commandCatalog.loading = true;
    state.commandCatalog.error = "";
    try {
      const payload = await requestSettingsJson("/api/commands");
      state.commandCatalog.commands = normalizeCommandCatalog(payload);
    } catch (error) {
      state.commandCatalog.error = error?.message || "Command catalog unavailable";
    } finally {
      state.commandCatalog.loading = false;
    }
  }

  async function loadCuratorStatus(options = {}) {
    const sessionId = String(options?.sessionId || getCuratorSessionId(currentSession.value)).trim();
    const quiet = Boolean(options?.quiet);
    if (!sessionId) {
      clearCuratorPollTimer();
      curatorState.loading = false;
      curatorState.status = null;
      curatorState.error = "";
      return null;
    }
    if (!quiet) {
      curatorState.loading = true;
      curatorState.error = "";
    }
    try {
      const payload = await requestSettingsJson(buildCuratorStatusPath(sessionId));
      const status = payload?.status || null;
      if (isCurrentCuratorSessionId(sessionId)) {
        curatorState.status = status;
        curatorState.error = "";
        scheduleCuratorPoll(status, sessionId);
      }
      return status;
    } catch (error) {
      if (isCurrentCuratorSessionId(sessionId)) {
        clearCuratorPollTimer();
        curatorState.error = error?.message || copy.value.curator.unavailable;
      }
      return null;
    } finally {
      if (!quiet && isCurrentCuratorSessionId(sessionId)) {
        curatorState.loading = false;
      }
    }
  }

  async function loadCuratorHistory(options = {}) {
    const sessionId = String(options?.sessionId || getCuratorSessionId(currentSession.value)).trim();
    const quiet = Boolean(options?.quiet);
    const limit = coerceNonNegativeInteger(options?.limit) || CURATOR_HISTORY_LIMIT;
    if (!sessionId) {
      curatorState.historyLoading = false;
      curatorState.history = [];
      curatorState.historyError = "";
      return [];
    }
    if (!quiet) {
      curatorState.historyLoading = true;
      curatorState.historyError = "";
    }
    try {
      const payload = await requestSettingsJson(buildCuratorHistoryPath(sessionId, limit));
      const history = Array.isArray(payload?.history) ? payload.history : [];
      if (isCurrentCuratorSessionId(sessionId)) {
        curatorState.history = history;
        curatorState.historyError = "";
      }
      return history;
    } catch (error) {
      if (isCurrentCuratorSessionId(sessionId)) {
        curatorState.historyError = error?.message || copy.value.curator.historyUnavailable;
      }
      return [];
    } finally {
      if (!quiet && isCurrentCuratorSessionId(sessionId)) {
        curatorState.historyLoading = false;
      }
    }
  }

  async function refreshCuratorState(options = {}) {
    const sessionId = String(options?.sessionId || getCuratorSessionId(currentSession.value)).trim();
    const quiet = Boolean(options?.quiet);
    if (!sessionId) {
      curatorState.loading = false;
      curatorState.status = null;
      curatorState.error = "";
      curatorState.history = [];
      curatorState.historyLoading = false;
      curatorState.historyError = "";
      return null;
    }
    if (!quiet) {
      curatorState.loading = true;
      curatorState.error = "";
    }
    try {
      const [status] = await Promise.all([
        loadCuratorStatus({ sessionId, quiet: true }),
        loadCuratorHistory({ sessionId, quiet: true, limit: options?.limit }),
      ]);
      return status;
    } finally {
      if (!quiet && isCurrentCuratorSessionId(sessionId)) {
        curatorState.loading = false;
      }
    }
  }

  async function runCuratorAction(action) {
    const normalizedAction = typeof action === "object" && action !== null
      ? String(action.action || "").trim()
      : String(action || "").trim();
    const scope = typeof action === "object" && action !== null
      ? String(action.scope || "").trim()
      : "";
    const sessionId = getCuratorSessionId(currentSession.value);
    if (!normalizedAction || !sessionId) {
      return null;
    }
    const actionToken = `${sessionId}\0${normalizedAction}\0${scope}\0${Date.now().toString(36)}-${randomToken()}`;
    curatorActionToken = actionToken;
    curatorState.action = normalizedAction;
    curatorState.error = "";
    try {
      const payload = await requestSettingsJson(
        buildCuratorActionPath(normalizedAction, sessionId, normalizedAction === "run" ? scope : ""),
        { method: "POST" },
      );
      const status = payload?.status || null;
      if (isCurrentCuratorSessionId(sessionId)) {
        curatorState.status = status;
        curatorState.error = "";
        scheduleCuratorPoll(status, sessionId);
      }
      return status;
    } catch (error) {
      if (isCurrentCuratorSessionId(sessionId)) {
        curatorState.error = error?.message || copy.value.curator.actionFailed;
      }
      return null;
    } finally {
      if (curatorActionToken === actionToken) {
        curatorActionToken = "";
        curatorState.action = "";
      }
    }
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
      artifact: normalizeTraceEventArtifact(eventType, eventPayload, event?.artifact, {
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

  async function loadBackgroundProcesses(options = {}) {
    const sessionId = String(options?.sessionId || "").trim();
    const quiet = Boolean(options?.quiet);
    const limit = coerceNonNegativeInteger(options?.limit) || BACKGROUND_PROCESS_LIMIT;
    if (state.backgroundProcesses.loading || clientDisposed) {
      return [];
    }

    if (!quiet) {
      state.backgroundProcesses.loading = true;
      state.backgroundProcesses.error = "";
    }
    try {
      const payload = await requestSettingsJson(buildBackgroundProcessesPath(sessionId, limit));
      const processes = Array.isArray(payload?.processes)
        ? payload.processes.map(normalizeBackgroundProcess).filter(Boolean)
        : [];
      state.backgroundProcesses.processes = processes;
      state.backgroundProcesses.counts = payload?.counts && typeof payload.counts === "object" ? payload.counts : {};
      state.backgroundProcesses.error = "";
      state.backgroundProcesses.lastLoadedAt = Date.now();
      return processes;
    } catch (error) {
      state.backgroundProcesses.error = error?.message || copy.value.sidebar.backgroundProcessesLoadFailed;
      return [];
    } finally {
      if (!quiet) {
        state.backgroundProcesses.loading = false;
      }
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
      scrollMessagesToBottom();
    } catch {
      setNotice(copy.value.notices.historyLoadFailed, "warning");
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
    if (sectionName === "general") {
      loadUpdateStatus();
      return;
    }
    if (sectionName === "channels") {
      loadChannelSettings();
      return;
    }
    if (sectionName === "providers") {
      loadProviderSettings();
      loadCodexAuthStatus();
      loadCopilotAuthStatus();
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
      return;
    }
    if (sectionName === "network") {
      loadNetworkSettings();
      return;
    }
    if (sectionName === "data") {
      loadDataSettings();
      return;
    }
    if (sectionName === "curator") {
      void refreshCuratorState();
    }
  }

  function cancelProviderConnect() {
    settingsState.connectForm.providerId = "";
    settingsState.connectForm.name = "";
    settingsState.connectForm.apiKey = "";
    settingsState.connectForm.baseUrl = "";
    settingsState.connectForm.showAdvanced = false;
  }

  async function startCodexAuthLogin() {
    clearCodexAuthPollTimer();
    settingsState.codexAuthLoading = true;
    settingsState.codexAuthError = "";
    settingsState.codexAuthNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/auth/openai-codex/login", { method: "POST" });
      settingsState.codexAuth = {
        ...settingsState.codexAuth,
        command: "",
        verificationUri: payload.verification_uri || "",
        userCode: payload.user_code || "",
        deviceAuthId: payload.device_auth_id || "",
        pollIntervalSeconds: coerceNonNegativeInteger(payload.interval) || 5,
      };
      if (settingsState.codexAuth.verificationUri) {
        window.open(settingsState.codexAuth.verificationUri, "_blank", "noopener,noreferrer");
      }
      setSettingsSuccess("codexAuthNotice", copy.value.notices.codexAuthLoginReady);
      scheduleCodexAuthPoll();
    } catch (error) {
      settingsState.codexAuthError = error?.message || copy.value.notices.codexAuthLoginFailed;
    } finally {
      settingsState.codexAuthLoading = false;
    }
  }

  function clearCodexAuthPollTimer() {
    if (codexAuthPollTimer) {
      clearTimeout(codexAuthPollTimer);
      codexAuthPollTimer = null;
    }
  }

  function scheduleCodexAuthPoll() {
    clearCodexAuthPollTimer();
    const delayMs = Math.max(3, settingsState.codexAuth.pollIntervalSeconds || 5) * 1000;
    codexAuthPollTimer = window.setTimeout(() => {
      void pollCodexAuthLogin();
    }, delayMs);
  }

  async function pollCodexAuthLogin() {
    const deviceAuthId = settingsState.codexAuth.deviceAuthId;
    const userCode = settingsState.codexAuth.userCode;
    if (!deviceAuthId || !userCode) {
      return;
    }
    try {
      const payload = await requestSettingsJson("/api/settings/auth/openai-codex/poll", {
        method: "POST",
        body: JSON.stringify({ device_auth_id: deviceAuthId, user_code: userCode }),
      });
      if (payload.status === "authorized") {
        const auth = payload.auth || {};
        settingsState.codexAuth = {
          ...settingsState.codexAuth,
          configured: Boolean(auth.configured),
          expired: Boolean(auth.expired),
          expires_at: auth.expires_at || null,
          account_id: auth.account_id || "",
          path: auth.path || settingsState.codexAuth.path,
          verificationUri: "",
          userCode: "",
          deviceAuthId: "",
        };
        setSettingsSuccess("codexAuthNotice", copy.value.notices.codexAuthLoginComplete);
        await loadModelSettings();
        return;
      }
      scheduleCodexAuthPoll();
    } catch (error) {
      settingsState.codexAuthError = error?.message || copy.value.notices.codexAuthLoginFailed;
      clearCodexAuthPollTimer();
    }
  }

  async function logoutCodexAuth() {
    clearCodexAuthPollTimer();
    settingsState.codexAuthLoading = true;
    settingsState.codexAuthError = "";
    settingsState.codexAuthNotice = "";
    try {
      await requestSettingsJson("/api/settings/auth/openai-codex/logout", { method: "POST" });
      settingsState.codexAuth = {
        ...settingsState.codexAuth,
        configured: false,
        expired: false,
        expires_at: null,
        account_id: "",
        command: "",
        verificationUri: "",
        userCode: "",
        deviceAuthId: "",
      };
      setSettingsSuccess("codexAuthNotice", copy.value.notices.codexAuthLoggedOut);
      await loadCodexAuthStatus();
    } catch (error) {
      settingsState.codexAuthError = error?.message || copy.value.notices.codexAuthLogoutFailed;
    } finally {
      settingsState.codexAuthLoading = false;
    }
  }

  async function startCopilotAuthLogin() {
    clearCopilotAuthPollTimer();
    settingsState.copilotAuthLoading = true;
    settingsState.copilotAuthError = "";
    settingsState.copilotAuthNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/auth/copilot/login", { method: "POST" });
      settingsState.copilotAuth = {
        ...settingsState.copilotAuth,
        verificationUri: payload.verification_uri || "",
        userCode: payload.user_code || "",
        deviceCode: payload.device_code || "",
        pollIntervalSeconds: coerceNonNegativeInteger(payload.interval) || 5,
      };
      if (settingsState.copilotAuth.verificationUri) {
        window.open(settingsState.copilotAuth.verificationUri, "_blank", "noopener,noreferrer");
      }
      setSettingsSuccess("copilotAuthNotice", copy.value.notices.copilotAuthLoginReady);
      scheduleCopilotAuthPoll();
    } catch (error) {
      settingsState.copilotAuthError = error?.message || copy.value.notices.copilotAuthLoginFailed;
    } finally {
      settingsState.copilotAuthLoading = false;
    }
  }

  function clearCopilotAuthPollTimer() {
    if (copilotAuthPollTimer) {
      clearTimeout(copilotAuthPollTimer);
      copilotAuthPollTimer = null;
    }
  }

  function scheduleCopilotAuthPoll() {
    clearCopilotAuthPollTimer();
    const delayMs = Math.max(3, settingsState.copilotAuth.pollIntervalSeconds || 5) * 1000;
    copilotAuthPollTimer = window.setTimeout(() => {
      void pollCopilotAuthLogin();
    }, delayMs);
  }

  async function pollCopilotAuthLogin() {
    const deviceCode = settingsState.copilotAuth.deviceCode;
    if (!deviceCode) return;
    try {
      const payload = await requestSettingsJson("/api/settings/auth/copilot/poll", {
        method: "POST",
        body: JSON.stringify({ device_code: deviceCode }),
      });
      if (payload.status === "authorized") {
        const auth = payload.auth || {};
        settingsState.copilotAuth = {
          ...settingsState.copilotAuth,
          configured: Boolean(auth.configured),
          path: auth.path || settingsState.copilotAuth.path,
          verificationUri: "",
          userCode: "",
          deviceCode: "",
        };
        setSettingsSuccess("copilotAuthNotice", copy.value.notices.copilotAuthLoginComplete);
        await loadModelSettings();
        return;
      }
      scheduleCopilotAuthPoll();
    } catch (error) {
      settingsState.copilotAuthError = error?.message || copy.value.notices.copilotAuthLoginFailed;
      clearCopilotAuthPollTimer();
    }
  }

  async function logoutCopilotAuth() {
    clearCopilotAuthPollTimer();
    settingsState.copilotAuthLoading = true;
    settingsState.copilotAuthError = "";
    settingsState.copilotAuthNotice = "";
    try {
      await requestSettingsJson("/api/settings/auth/copilot/logout", { method: "POST" });
      settingsState.copilotAuth = { ...settingsState.copilotAuth, configured: false, path: "", verificationUri: "", userCode: "", deviceCode: "" };
      setSettingsSuccess("copilotAuthNotice", copy.value.notices.copilotAuthLoggedOut);
      await loadCopilotAuthStatus();
    } catch (error) {
      settingsState.copilotAuthError = error?.message || copy.value.notices.copilotAuthLogoutFailed;
    } finally {
      settingsState.copilotAuthLoading = false;
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
      setSettingsSuccess("cronJobsNotice", jobId ? copy.value.notices.cronJobUpdated : copy.value.notices.cronJobCreated);
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
      setSettingsSuccess("cronJobsNotice", copy.value.notices.cronJobActionDone);
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
      addMessage(session.externalChatId, makeMessage("assistant", payload.text || "", "OpenSprite"));
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
      void loadBackgroundProcesses({ quiet: true });
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
        window.requestAnimationFrame?.(() => {
          stage.scrollTop = stage.scrollHeight;
        });
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
    saveRunPanelVisibilitySettings(
      settingsForm.showWorkState,
      settingsForm.showRunHistory,
      settingsForm.showRunTimeline,
      settingsForm.showRunSummary,
      settingsForm.showRunTrace,
    );

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
    void loadCommandCatalog();

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

  function normalizeOutgoingMessage(rawValue) {
    if (rawValue && typeof rawValue === "object" && !Array.isArray(rawValue)) {
      return {
        text: String(rawValue.text || "").trim(),
        metadata: rawValue.metadata && typeof rawValue.metadata === "object" ? rawValue.metadata : {},
      };
    }
    return { text: String(rawValue || "").trim(), metadata: {} };
  }

  function sendMessageText(rawText, { clearComposer = false } = {}) {
    const payload = normalizeOutgoingMessage(rawText);
    const text = payload.text;
    if (!text) {
      return false;
    }

    if (!activeSocket || activeSocket.readyState !== WebSocket.OPEN) {
      if (state.connectionState === "connecting") {
        setNotice(copy.value.notices.stillConnecting, "info");
        return false;
      }
      setNotice(copy.value.notices.inactiveConnection, "warning");
      openSettings("general");
      return false;
    }

    const session = currentSession.value;
    if (!session) {
      return false;
    }
    if (session.channel !== "web") {
      setNotice(copy.value.composer.readOnlyChannel(session.channel), "info");
      return false;
    }

    addMessage(session.externalChatId, makeMessage("user", text, state.displayName || "Local browser"));
    const outgoingMetadata = {
      overlay_profile_id: overlayProfileId.value,
      ...(payload.metadata && typeof payload.metadata === "object" ? payload.metadata : {}),
    };
    activeSocket.send(
      JSON.stringify({
        external_chat_id: session.externalChatId,
        ...(session.sessionId ? { session_id: session.sessionId } : {}),
        sender_name: state.displayName,
        text,
        metadata: outgoingMetadata,
      }),
    );

    if (clearComposer) {
      messageText.value = "";
      resizeComposer();
    }
    scrollMessagesToBottom();
    return true;
  }

  function submitMessage(event) {
    event.preventDefault();
    sendMessageText(messageText.value, { clearComposer: true });
  }

  function resumeFollowUp(text) {
    sendMessageText(text, { clearComposer: false });
  }

  function runVerification(text) {
    sendMessageText(text, { clearComposer: false });
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

  function applyCommandHint(command) {
    const token = String(command?.command || "").trim();
    if (!token) {
      return;
    }
    messageText.value = `${token} `;
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
    void loadCommandCatalog();
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
    clearCuratorPollTimer();
    clearCodexAuthPollTimer();
    clearCopilotAuthPollTimer();
    for (const timer of toastTimers.values()) {
      clearTimeout(timer);
    }
    toastTimers.clear();
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
    toasts,
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
    curatorState,
    currentCuratorStatus,
    currentSessionApiId,
    activeBackgroundProcesses,
    settingsTitle,
    sessionMeta,
    runtimeHint,
    composerHint,
    commandHints,
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
    selectBackgroundProcess,
    selectRun,
    selectSettingsSection,
    openSettings,
    closeSettings,
    saveConnectionSettings,
    loadProviderSettings,
    loadCodexAuthStatus,
    loadCopilotAuthStatus,
    loadUpdateStatus,
    loadModelSettings,
    loadChannelSettings,
    loadScheduleSettings,
    loadNetworkSettings,
    loadDataSettings,
    loadBackgroundProcesses,
    loadDataSessionTimeline,
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
    setProviderCredential,
    deleteCredential,
    connectCodexProvider,
    connectOAuthProvider,
    connectCopilotProvider,
    startCodexAuthLogin,
    logoutCodexAuth,
    startCopilotAuthLogin,
    logoutCopilotAuth,
    runUpdate,
    selectModel,
    applyOpenRouterRecommendedOptions,
    saveOpenRouterOptions,
    saveLlmSettings,
    saveMediaModel,
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
    saveNetworkSettings,
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
    loadCuratorStatus,
    refreshCuratorState,
    runCuratorAction,
    resolvePermissionRequest,
    toggleSettingsConnection,
    submitMessage,
    resumeFollowUp,
    runVerification,
    handleComposerKeydown,
    applyPrompt,
    applyCommandHint,
    dismissToast,
  };
}
