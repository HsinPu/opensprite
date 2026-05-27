const MAX_RUN_EVENTS = 80;
const MAX_RUN_TEXT_EVENTS = 24;

const RUN_EVENT_KINDS = new Set(["run", "llm", "tool", "verification", "permission", "work", "harness", "completion", "file", "process", "text", "system", "other"]);

function randomToken() {
  return Math.random().toString(36).slice(2, 8);
}

function previewText(value) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }
  return normalized.length > 96 ? `${normalized.slice(0, 96)}...` : normalized;
}

function coerceStringList(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function compactJoin(values, separator = " · ") {
  return values.map((value) => String(value || "").trim()).filter(Boolean).join(separator);
}

function countItems(value) {
  return Array.isArray(value) ? value.length : 0;
}

function coerceText(value) {
  return String(value || "").trim();
}

function formatShortList(value, maxItems = 3) {
  const items = coerceStringList(value);
  if (!items.length) {
    return "";
  }
  const visible = items.slice(0, maxItems).join(", ");
  const remaining = items.length - maxItems;
  return remaining > 0 ? `${visible} +${remaining}` : visible;
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

function normalizeEventTimestamp(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue) || numericValue <= 0) {
    return Date.now();
  }
  return numericValue > 1_000_000_000_000 ? numericValue : numericValue * 1000;
}

export function coerceEventPayload(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function normalizeRunKind(value, fallback = "other") {
  const normalized = String(value || "").trim();
  return RUN_EVENT_KINDS.has(normalized) ? normalized : fallback;
}

export function inferRunEventKind(eventType) {
  const normalized = String(eventType || "").trim();
  if (normalized === "run_part_delta" || normalized === "message_part_delta") {
    return "text";
  }
  if (normalized.startsWith("run_") || normalized.startsWith("auto_continue.")) {
    return "run";
  }
  if (normalized.startsWith("llm_") || normalized === "reasoning_delta" || normalized === "execution.stopped") {
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
  if (normalized.startsWith("harness_") || normalized.startsWith("task_contract.")) {
    return "harness";
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

export function inferRunEventStatus(eventType, payload = {}) {
  const normalized = String(eventType || "").trim();
  const explicit = String(payload.status || payload.state || "").trim();
  if (explicit) {
    return explicit;
  }
  if (normalized === "run_part_delta" || normalized === "message_part_delta") {
    return "running";
  }
  if (normalized === "execution.stopped") {
    return "stopped";
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

export function compactRunEvents(events) {
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

export function normalizeTraceEventCounts(counts, events = []) {
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

export function updateLiveTraceEventCounts(run, event) {
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

export function normalizeRunArtifact(artifact, fallback = {}) {
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

function normalizeDecisionStatus(status) {
  const normalized = coerceText(status).toLowerCase();
  if (["completed", "complete", "passed", "pass", "success", "ok", "ready"].includes(normalized)) {
    return "success";
  }
  if (["failed", "fail", "error"].includes(normalized)) {
    return "failed";
  }
  if (["blocked", "denied", "cancelled", "canceled"].includes(normalized)) {
    return "blocked";
  }
  if (["running", "scheduled", "incomplete", "waiting", "waiting_user", "cancelling"].includes(normalized)) {
    return "warning";
  }
  return "info";
}

function decisionDetail(labelKey, value, tone = "neutral") {
  const normalized = value === null || value === undefined ? "" : String(value).trim();
  if (!normalized) {
    return null;
  }
  return { labelKey, value: normalized, tone };
}

function compactDetails(items) {
  return items.filter(Boolean);
}

function profileName(payload) {
  return compactJoin([
    payload?.name,
    payload?.task_type || payload?.taskType,
  ], " / ");
}

function decisionId(event, index) {
  return `decision:${event.id || event.eventId || event.event_id || event.eventType || event.event_type || "event"}:${index}`;
}

function decisionEventId(event) {
  const eventId = event?.id || event?.eventId || event?.event_id;
  return eventId ? [String(eventId)] : [];
}

function profileDecision(eventType, payload, event, index) {
  const selection = payload.selection || {};
  const titleKey = eventType === "harness_profile.effective_selected"
    ? "effectiveProfile"
    : eventType === "harness_profile.initial_selected"
      ? "initialProfile"
      : "profileSelected";
  return {
    id: decisionId(event, index),
    eventIds: decisionEventId(event),
    phase: "profile",
    status: "info",
    titleKey,
    title: "Profile selected",
    summary: compactJoin([profileName(payload), payload.reason]),
    reason: coerceText(payload.reason),
    createdAt: event.createdAt,
    details: compactDetails([
      decisionDetail("profile", payload.name),
      decisionDetail("taskType", payload.task_type || payload.taskType),
      decisionDetail("selection", compactJoin([selection.selected_by || selection.selectedBy, formatShortList(selection.matched_signals || selection.matchedSignals, 4)])),
      decisionDetail("verification", payload.verification_policy || payload.verificationPolicy),
      decisionDetail("continuation", payload.continuation_policy || payload.continuationPolicy),
    ]),
  };
}

function profileChangedDecision(payload, event, index) {
  const initial = payload.initial || {};
  const effective = payload.effective || {};
  return {
    id: decisionId(event, index),
    eventIds: decisionEventId(event),
    phase: "profile",
    status: "warning",
    titleKey: "profileChanged",
    title: "Profile changed",
    summary: compactJoin([`${profileName(initial) || "unknown"} -> ${profileName(effective) || "unknown"}`, payload.reason], " / "),
    reason: coerceText(payload.reason),
    createdAt: event.createdAt,
    details: compactDetails([
      decisionDetail("initialProfile", profileName(initial)),
      decisionDetail("effectiveProfile", profileName(effective)),
      decisionDetail("reason", payload.reason),
    ]),
  };
}

function policySelectedDecision(payload, event, index) {
  return {
    id: decisionId(event, index),
    eventIds: decisionEventId(event),
    phase: "policy",
    status: "info",
    titleKey: "policySelected",
    title: "Policy selected",
    summary: compactJoin([payload.name, payload.reason]),
    reason: coerceText(payload.reason),
    createdAt: event.createdAt,
    details: compactDetails([
      decisionDetail("policy", payload.name),
      decisionDetail("allowedTools", formatShortList(payload.allowed_tools || payload.allowedTools, 5)),
      decisionDetail("allowedRisks", formatShortList(payload.allowed_risk_levels || payload.allowedRiskLevels, 5)),
      decisionDetail("deniedRisks", formatShortList(payload.denied_risk_levels || payload.deniedRiskLevels, 5), "warning"),
      decisionDetail("approvalRisks", formatShortList(payload.approval_required_risk_levels || payload.approvalRequiredRiskLevels, 5)),
    ]),
  };
}

function policyMergedDecision(payload, event, index) {
  const policy = payload.harness_policy || payload.harnessPolicy || {};
  const blocked = countItems(payload.blocked_relaxations || payload.blockedRelaxations);
  return {
    id: decisionId(event, index),
    eventIds: decisionEventId(event),
    phase: "policy",
    status: blocked > 0 ? "warning" : "success",
    titleKey: "policyMerged",
    title: "Policy merged",
    summary: compactJoin([
      policy.name,
      `${countItems(payload.constraints_applied || payload.constraintsApplied)} constraints`,
      blocked ? `${blocked} blocked relaxations` : "",
    ]),
    reason: "",
    createdAt: event.createdAt,
    details: compactDetails([
      decisionDetail("policy", policy.name),
      decisionDetail("constraints", formatShortList(payload.constraints_applied || payload.constraintsApplied, 5)),
      decisionDetail("blockedRelaxations", formatShortList(payload.blocked_relaxations || payload.blockedRelaxations, 5), blocked > 0 ? "warning" : "neutral"),
    ]),
  };
}

function taskContractDecision(payload, event, index) {
  return {
    id: decisionId(event, index),
    eventIds: decisionEventId(event),
    phase: "contract",
    status: "success",
    titleKey: event.eventType === "task_contract.semantic_classified" ? "semanticContract" : "taskContract",
    title: "Task contract",
    summary: compactJoin([
      payload.task_type || payload.taskType,
      `${countItems(payload.requirements)} requirements`,
      `${countItems(payload.acceptance_criteria || payload.acceptanceCriteria)} criteria`,
    ]),
    reason: "",
    createdAt: event.createdAt,
    details: compactDetails([
      decisionDetail("taskType", payload.task_type || payload.taskType),
      decisionDetail("requirements", countItems(payload.requirements)),
      decisionDetail("criteria", countItems(payload.acceptance_criteria || payload.acceptanceCriteria)),
      decisionDetail("sources", formatShortList(payload.contract_sources || payload.contractSources, 5)),
    ]),
  };
}

function completionGateDecision(payload, event, index) {
  const missingEvidence = payload.missing_evidence || payload.missingEvidence;
  return {
    id: decisionId(event, index),
    eventIds: decisionEventId(event),
    phase: "completion",
    status: normalizeDecisionStatus(payload.status || (payload.ok === false ? "failed" : "completed")),
    titleKey: "completionGate",
    title: "Completion gate",
    summary: compactJoin([payload.status, payload.reason, countItems(missingEvidence) ? `${countItems(missingEvidence)} missing` : ""]),
    reason: coerceText(payload.reason),
    createdAt: event.createdAt,
    details: compactDetails([
      decisionDetail("status", payload.status),
      decisionDetail("reason", payload.reason),
      decisionDetail("missingEvidence", formatShortList(missingEvidence, 4), countItems(missingEvidence) ? "warning" : "neutral"),
      decisionDetail("attempts", payload.auto_continue_attempts ?? payload.autoContinueAttempts),
    ]),
  };
}

function autoContinueDecision(eventType, payload, event, index) {
  const action = eventType.replace("auto_continue.", "");
  return {
    id: decisionId(event, index),
    eventIds: decisionEventId(event),
    phase: "completion",
    status: action === "scheduled" ? "warning" : action === "skipped" ? "blocked" : "success",
    titleKey: "autoContinue",
    title: "Auto-continue",
    summary: compactJoin([action, payload.reason]),
    reason: coerceText(payload.reason),
    createdAt: event.createdAt,
    details: compactDetails([
      decisionDetail("status", action),
      decisionDetail("reason", payload.reason),
      decisionDetail("attempts", payload.attempt ?? payload.attempts ?? payload.auto_continue_attempts ?? payload.autoContinueAttempts),
    ]),
  };
}

function checkpointDecision(payload, event, index) {
  const completion = payload.completion || {};
  const progress = payload.work_progress || payload.workProgress || {};
  return {
    id: decisionId(event, index),
    eventIds: decisionEventId(event),
    phase: "checkpoint",
    status: "success",
    titleKey: "checkpoint",
    title: "Checkpoint recorded",
    summary: compactJoin([payload.next_action || payload.nextAction || progress.next_action || progress.nextAction, completion.status]),
    reason: coerceText(completion.reason),
    createdAt: event.createdAt,
    details: compactDetails([
      decisionDetail("nextAction", payload.next_action || payload.nextAction || progress.next_action || progress.nextAction),
      decisionDetail("status", completion.status),
      decisionDetail("reason", completion.reason),
      decisionDetail("artifacts", payload.task_artifact_count ?? payload.taskArtifactCount),
      decisionDetail("attempts", payload.auto_continue_attempts ?? payload.autoContinueAttempts),
    ]),
  };
}

function scorecardDecision(payload, event, index) {
  const profile = payload.profile || {};
  const contract = payload.contract || {};
  const completion = payload.completion || {};
  const traceHealth = payload.trace_health || payload.traceHealth || {};
  const sensors = Array.isArray(payload.sensors) ? payload.sensors : [];
  return {
    id: decisionId(event, index),
    eventIds: decisionEventId(event),
    phase: "checkpoint",
    status: traceHealth.status === "fail" ? "failed" : traceHealth.status === "warn" ? "warning" : "success",
    titleKey: "scorecard",
    title: "Harness scorecard",
    summary: compactJoin([profile.name || contract.task_type || contract.taskType, completion.status, traceHealth.status]),
    reason: coerceText(completion.reason),
    createdAt: event.createdAt,
    details: compactDetails([
      decisionDetail("profile", profile.name),
      decisionDetail("taskType", contract.task_type || contract.taskType),
      decisionDetail("status", completion.status),
      decisionDetail("reason", completion.reason),
      decisionDetail("traceHealth", traceHealth.status),
      decisionDetail("sensors", sensors.length),
    ]),
  };
}

function evalDecision(eventType, payload, event, index) {
  const summary = payload.summary || {};
  return {
    id: decisionId(event, index),
    eventIds: decisionEventId(event),
    phase: "checkpoint",
    status: payload.ok === true ? "success" : "failed",
    titleKey: eventType === "harness_eval.completed" ? "evalCompleted" : "evalFailed",
    title: "Harness eval",
    summary: compactJoin([
      payload.kind,
      payload.ok === true ? "pass" : "fail",
      summary.total_cases !== undefined ? `${summary.passed_cases}/${summary.total_cases} cases` : "",
      summary.total_checks !== undefined ? `${summary.passed_checks}/${summary.total_checks} checks` : "",
    ]),
    reason: coerceText(payload.reason || payload.error),
    createdAt: event.createdAt,
    details: compactDetails([
      decisionDetail("kind", payload.kind),
      decisionDetail("status", payload.ok === true ? "pass" : "fail", payload.ok === true ? "success" : "error"),
      decisionDetail("cases", summary.total_cases !== undefined ? `${summary.passed_cases}/${summary.total_cases}` : ""),
      decisionDetail("checks", summary.total_checks !== undefined ? `${summary.passed_checks}/${summary.total_checks}` : ""),
      decisionDetail("reason", payload.reason || payload.error, payload.ok === true ? "neutral" : "error"),
    ]),
  };
}

export function deriveDecisionTimelineItems(events = []) {
  if (!Array.isArray(events)) {
    return [];
  }
  const items = [];
  for (const event of events) {
    const eventType = coerceText(event?.eventType || event?.event_type);
    const payload = coerceEventPayload(event?.payload);
    const eventWithTimestamp = {
      ...event,
      eventType,
      createdAt: normalizeEventTimestamp(event?.createdAt ?? event?.created_at),
    };
    let item = null;
    if (eventType === "harness_profile.selected" || eventType === "harness_profile.initial_selected" || eventType === "harness_profile.effective_selected") {
      item = profileDecision(eventType, payload, eventWithTimestamp, items.length);
    } else if (eventType === "harness_profile.changed") {
      item = profileChangedDecision(payload, eventWithTimestamp, items.length);
    } else if (eventType === "harness_policy.selected") {
      item = policySelectedDecision(payload, eventWithTimestamp, items.length);
    } else if (eventType === "harness_policy.merge_resolved") {
      item = policyMergedDecision(payload, eventWithTimestamp, items.length);
    } else if (eventType === "task_contract.created" || eventType === "task_contract.semantic_classified") {
      item = taskContractDecision(payload, eventWithTimestamp, items.length);
    } else if (eventType === "completion_gate.evaluated") {
      item = completionGateDecision(payload, eventWithTimestamp, items.length);
    } else if (eventType.startsWith("auto_continue.")) {
      item = autoContinueDecision(eventType, payload, eventWithTimestamp, items.length);
    } else if (eventType === "harness_checkpoint.recorded") {
      item = checkpointDecision(payload, eventWithTimestamp, items.length);
    } else if (eventType === "harness_scorecard.recorded") {
      item = scorecardDecision(payload, eventWithTimestamp, items.length);
    } else if (eventType.startsWith("harness_eval.")) {
      item = evalDecision(eventType, payload, eventWithTimestamp, items.length);
    }
    if (item) {
      items.push(item);
    }
  }
  return items;
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
      output_path: String(payload.output_path || payload.outputPath || "").trim(),
    },
  };
}

export function normalizeTraceEventArtifact(eventType, payload, artifact, fallback = {}) {
  return normalizeRunArtifact(artifact, fallback)
    || normalizeBackgroundProcessArtifact(eventType, payload, fallback);
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

export function findWorktreeSandbox(parts = [], artifacts = []) {
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

export function normalizeWorkState(payload) {
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

export function normalizeTraceEvent(event) {
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

export function normalizeTracePart(part) {
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

export function normalizeTraceFileChange(change) {
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
