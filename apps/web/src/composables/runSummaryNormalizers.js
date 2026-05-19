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

function normalizeEventTimestamp(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue) || numericValue <= 0) {
    return Date.now();
  }
  return numericValue > 1_000_000_000_000 ? numericValue : numericValue * 1000;
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

export function normalizeRunSummary(payload) {
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
