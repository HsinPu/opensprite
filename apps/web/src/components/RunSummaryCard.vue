<template>
  <section class="run-summary-card" :data-status="status" :data-collapsed="!expanded" aria-live="polite">
    <div class="run-summary-card__header">
      <div class="run-summary-card__title">
        <span class="run-summary-card__eyebrow">{{ copy.runSummary.title }}</span>
        <strong>{{ objective }}</strong>
      </div>
      <div class="run-summary-card__actions">
        <button class="run-summary-card__copy" type="button" :aria-expanded="expanded" @click="expanded = !expanded">
          {{ expanded ? copy.runSummary.collapse : copy.runSummary.expand }}
        </button>
        <button
          v-if="summary"
          class="run-summary-card__copy"
          type="button"
          :disabled="copyState === 'copying'"
          @click="copyReport"
        >
          {{ copyButtonLabel }}
        </button>
        <button
          v-if="summary"
          class="run-summary-card__copy"
          type="button"
          @click="downloadReport"
        >
          {{ copy.runSummary.downloadReport }}
        </button>
        <span class="run-summary-card__status">{{ statusLabel }}</span>
      </div>
    </div>

    <div v-show="expanded" class="run-summary-card__body">
      <p v-if="run.summaryLoading && !summary" class="run-summary-card__message">
        {{ copy.runSummary.loading }}
      </p>

      <p v-else-if="run.summaryError && !summary" class="run-summary-card__message" data-tone="error">
        {{ copy.runSummary.unavailable }}: {{ run.summaryError }}
      </p>

      <template v-if="summary">
        <dl class="run-summary-card__metrics">
          <div>
            <dt>{{ copy.runSummary.duration }}</dt>
            <dd>{{ durationLabel }}</dd>
          </div>
          <div>
            <dt>{{ copy.runSummary.tools }}</dt>
            <dd>{{ copy.runSummary.totalToolCalls(toolCallCount) }}</dd>
          </div>
          <div>
            <dt>{{ copy.runSummary.files }}</dt>
            <dd>{{ copy.runSummary.fileCount(fileChangeCount) }}</dd>
          </div>
          <div>
            <dt>{{ copy.runSummary.events }}</dt>
            <dd>{{ copy.runSummary.eventCount(summary.counts.events) }}</dd>
          </div>
        </dl>

        <div class="run-summary-card__note" :data-tone="verificationTone">
          <strong>{{ copy.runSummary.verification }}</strong>
          <span>{{ verificationLabel }}</span>
          <small v-if="summary.verification.summary">{{ summary.verification.summary }}</small>
        </div>

        <div v-if="parallelDelegation.groupCount > 0" class="run-summary-card__note" :data-tone="parallelDelegationTone">
          <strong>{{ copy.runSummary.parallelDelegation }}</strong>
          <span>{{ parallelDelegationLabel }}</span>
          <small v-if="parallelDelegationDetail">{{ parallelDelegationDetail }}</small>
        </div>

        <div v-if="parallelDelegation.groups.length" class="run-summary-card__chips">
          <span>{{ copy.runSummary.parallelDelegationGroups }}</span>
          <code v-for="group in visibleParallelGroups" :key="group.groupId">
            {{ parallelGroupChip(group) }}
          </code>
        </div>

        <div v-if="hasDiffSummary" class="run-summary-card__diff">
          <div class="run-summary-card__diff-header">
            <strong>{{ copy.runSummary.diffSummary }}</strong>
            <span>+{{ diffSummary.additions }} / -{{ diffSummary.deletions }}</span>
          </div>
          <dl class="run-summary-card__diff-metrics">
            <div>
              <dt>{{ copy.runSummary.changedFiles }}</dt>
              <dd>{{ diffSummary.changedFiles }}</dd>
            </div>
            <div>
              <dt>{{ copy.runSummary.changes }}</dt>
              <dd>{{ diffSummary.changeCount }}</dd>
            </div>
            <div>
              <dt>{{ copy.runSummary.additions }}</dt>
              <dd>+{{ diffSummary.additions }}</dd>
            </div>
            <div>
              <dt>{{ copy.runSummary.deletions }}</dt>
              <dd>-{{ diffSummary.deletions }}</dd>
            </div>
          </dl>
          <div v-if="diffActionEntries.length" class="run-summary-card__chips">
            <span>{{ copy.runSummary.actions }}</span>
            <code v-for="[action, count] in diffActionEntries" :key="action" :data-action="action">{{ action }} x{{ count }}</code>
          </div>
          <div v-if="visibleDiffPaths.length" class="run-summary-card__paths">
            <span>{{ copy.runSummary.paths }}</span>
            <button
              v-for="item in visibleDiffPathItems"
              :key="item.path"
              class="run-summary-card__path-button"
              type="button"
              :disabled="!item.change"
              @click="item.change && $emit('inspect-file', item.change)"
            >
              <code>{{ item.path }}</code>
              <small v-if="item.change">{{ item.change.action || copy.runSummary.inspectFile }}</small>
            </button>
            <small v-if="hiddenDiffPathCount > 0">{{ copy.runSummary.moreFiles(hiddenDiffPathCount) }}</small>
          </div>
        </div>

        <div v-if="worktreeSandbox" class="run-summary-card__sandbox">
          <div class="run-summary-card__sandbox-body">
            <strong>{{ copy.runSummary.worktreeSandbox }}</strong>
            <span>{{ worktreeSandbox.sandboxPath }}</span>
            <small v-if="worktreeSandbox.status">{{ worktreeSandbox.status }}</small>
            <small v-if="worktreeCleanupDetail">{{ worktreeCleanupDetail }}</small>
          </div>
          <button
            class="run-summary-card__copy"
            type="button"
            :disabled="!canCleanupWorktree || worktreeSandbox.cleanupPending"
            @click="$emit('cleanup-worktree', run)"
          >
            {{ worktreeSandbox.cleanupPending ? copy.runSummary.cleanupRunning : cleanupButtonLabel }}
          </button>
        </div>

        <div v-if="summary.tools.length" class="run-summary-card__chips">
          <span>{{ copy.runSummary.tools }}</span>
          <code v-for="tool in visibleTools" :key="tool.name">{{ tool.name }} x{{ tool.count }}</code>
        </div>

        <div v-if="visibleFileChanges.length" class="run-summary-card__files">
          <span>{{ copy.runSummary.files }}</span>
          <button
            v-for="change in visibleFileChanges"
            :key="`${change.action}:${change.path}`"
            class="run-summary-card__file-button"
            type="button"
            @click="$emit('inspect-file', change)"
          >
            <code>{{ change.action || "change" }} {{ change.path }}</code>
            <small>{{ change.toolName || copy.runSummary.inspectFile }}</small>
          </button>
          <small v-if="hiddenFileChangeCount > 0">{{ copy.runSummary.moreFiles(hiddenFileChangeCount) }}</small>
        </div>

        <div v-else class="run-summary-card__chips">
          <span>{{ copy.runSummary.files }}</span>
          <small>{{ copy.runSummary.noFiles }}</small>
        </div>

        <div v-if="summary.nextAction" class="run-summary-card__note">
          <strong>{{ copy.runSummary.nextAction }}</strong>
          <span>{{ summary.nextAction }}</span>
        </div>

        <div v-if="summary.warnings.length" class="run-summary-card__note" data-tone="warning">
          <strong>{{ copy.runSummary.warnings }}</strong>
          <span>{{ summary.warnings.join(", ") }}</span>
        </div>

        <div v-if="reportFallbackOpen" class="run-summary-card__report-fallback">
          <span>{{ copy.runSummary.reportFallback }}</span>
          <label>
            {{ copy.runSummary.reportTextLabel }}
            <textarea ref="reportTextarea" :value="reportText" readonly rows="8"></textarea>
          </label>
        </div>
      </template>
    </div>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from "vue";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  run: {
    type: Object,
    required: true,
  },
});

defineEmits(["inspect-file", "cleanup-worktree"]);

const summary = computed(() => props.run.summary || null);
const expanded = ref(false);
const copyState = ref("idle");
const reportFallbackOpen = ref(false);
const reportText = ref("");
const reportTextarea = ref(null);
let copyResetTimer = null;

const status = computed(() => summary.value?.status || props.run.status || "completed");

const statusLabel = computed(() => props.copy.run.statusLabels[status.value] || status.value);

const objective = computed(() => summary.value?.objective || props.copy.runSummary.fallbackObjective);

const durationLabel = computed(() => {
  const seconds = summary.value?.durationSeconds;
  return Number.isFinite(seconds) ? props.copy.runSummary.durationSeconds(seconds) : props.copy.runSummary.noDuration;
});

const toolCallCount = computed(() => {
  const fromCounts = summary.value?.counts?.toolCalls || 0;
  if (fromCounts > 0) {
    return fromCounts;
  }
  return (summary.value?.tools || []).reduce((total, tool) => total + tool.count, 0);
});

const fileChangeCount = computed(() => {
  return summary.value?.counts?.fileChanges || summary.value?.fileChanges?.length || 0;
});

const visibleTools = computed(() => (summary.value?.tools || []).slice(0, 4));

const visibleFileChanges = computed(() => (summary.value?.fileChanges || []).slice(0, 3));

const hiddenFileChangeCount = computed(() => {
  return Math.max(0, (summary.value?.fileChanges?.length || 0) - visibleFileChanges.value.length);
});

const verificationLabel = computed(() => {
  if (!summary.value?.verification?.attempted) {
    return props.copy.runSummary.verificationPending;
  }
  return summary.value.verification.passed
    ? props.copy.runSummary.verificationPassed
    : props.copy.runSummary.verificationFailed;
});

const verificationTone = computed(() => {
  if (!summary.value?.verification?.attempted) {
    return "neutral";
  }
  return summary.value.verification.passed ? "success" : "warning";
});

const parallelDelegation = computed(() => summary.value?.parallelDelegation || { groupCount: 0, taskCount: 0, groups: [] });

const visibleParallelGroups = computed(() => parallelDelegation.value.groups.slice(0, 4));

const parallelDelegationTone = computed(() => {
  if (!parallelDelegation.value.groupCount) {
    return "neutral";
  }
  if (parallelDelegation.value.groups.some((group) => group.status === "failed" || group.status === "error")) {
    return "warning";
  }
  if (parallelDelegation.value.groups.some((group) => group.status === "cancelled" || group.status === "cancelling")) {
    return "warning";
  }
  if (parallelDelegation.value.groups.some((group) => group.status === "running")) {
    return "neutral";
  }
  return "success";
});

const parallelDelegationLabel = computed(() => {
  const data = parallelDelegation.value;
  if (!data.groupCount) {
    return "";
  }
  return props.copy.runSummary.parallelDelegationSummary(data.groupCount, data.taskCount);
});

const parallelDelegationDetail = computed(() => {
  const firstSummary = parallelDelegation.value.groups.map((group) => group.summary).find(Boolean);
  if (firstSummary) {
    return firstSummary;
  }
  return parallelDelegation.value.groups
    .map((group) => parallelGroupChip(group))
    .join(" · ");
});

const diffSummary = computed(() => summary.value?.diffSummary || props.run.diffSummary || null);

const hasDiffSummary = computed(() => {
  const diff = diffSummary.value;
  if (!diff) {
    return false;
  }
  return diff.changedFiles > 0 || diff.changeCount > 0 || diff.additions > 0 || diff.deletions > 0 || diff.paths.length > 0;
});

const diffActionEntries = computed(() => Object.entries(diffSummary.value?.actions || {}).slice(0, 4));

const visibleDiffPaths = computed(() => (diffSummary.value?.paths || []).slice(0, 4));

const visibleDiffPathItems = computed(() => visibleDiffPaths.value.map((path) => ({
  path,
  change: (summary.value?.fileChanges || []).find((change) => change.path === path) || null,
})));

const hiddenDiffPathCount = computed(() => Math.max(0, (diffSummary.value?.paths?.length || 0) - visibleDiffPaths.value.length));

const worktreeSandbox = computed(() => props.run.worktreeSandbox || null);

const canCleanupWorktree = computed(() => Boolean(worktreeSandbox.value?.sandboxPath && worktreeSandbox.value?.cleanupSupported));

const cleanupButtonLabel = computed(() => {
  if (worktreeSandbox.value?.cleanupResult?.ok || worktreeSandbox.value?.status === "removed") {
    return props.copy.runSummary.cleanupDone;
  }
  return props.copy.runSummary.cleanupSandbox;
});

const worktreeCleanupDetail = computed(() => {
  const result = worktreeSandbox.value?.cleanupResult;
  if (!result) {
    return "";
  }
  return result.reason || result.repository_root || result.repositoryRoot || result.status || "";
});

const copyButtonLabel = computed(() => {
  if (copyState.value === "copying") {
    return props.copy.runSummary.copyingReport;
  }
  if (copyState.value === "copied") {
    return props.copy.runSummary.reportCopied;
  }
  if (copyState.value === "manual") {
    return props.copy.runSummary.manualCopy;
  }
  return props.copy.runSummary.copyReport;
});

onBeforeUnmount(() => {
  if (copyResetTimer) {
    clearTimeout(copyResetTimer);
  }
});

async function copyReport() {
  if (!summary.value) {
    return;
  }
  const report = buildRunReport();
  reportText.value = report;
  copyState.value = "copying";
  if (copyResetTimer) {
    clearTimeout(copyResetTimer);
    copyResetTimer = null;
  }
  try {
    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
      throw new Error("Clipboard API unavailable");
    }
    await navigator.clipboard.writeText(report);
    reportFallbackOpen.value = false;
    copyState.value = "copied";
    if (copyResetTimer) {
      clearTimeout(copyResetTimer);
    }
    copyResetTimer = setTimeout(() => {
      copyState.value = "idle";
    }, 1800);
  } catch {
    copyState.value = "manual";
    reportFallbackOpen.value = true;
    await nextTick();
    reportTextarea.value?.focus();
    reportTextarea.value?.select();
  }
}

function buildRunReport() {
  const data = summary.value;
  const lines = [
    `# ${props.copy.runSummary.reportTitle}`,
    "",
    `- ${props.copy.runFileInspector.run}: ${props.run.runId}`,
    `- ${props.copy.runFileInspector.session}: ${props.run.sessionId || data.sessionId || props.copy.runFileInspector.unknown}`,
    `- ${props.copy.runSummary.status}: ${statusLabel.value}`,
    `- ${props.copy.runSummary.objective}: ${objective.value}`,
    `- ${props.copy.runSummary.duration}: ${durationLabel.value}`,
    "",
    `## ${props.copy.runSummary.tools}`,
    ...formatTools(data.tools),
    "",
    `## ${props.copy.runSummary.verification}`,
    `- ${verificationLabel.value}`,
  ];

  if (data.verification.summary) {
    lines.push(`- ${data.verification.summary}`);
  }

  if (hasDiffSummary.value) {
    lines.push("", `## ${props.copy.runSummary.diffSummary}`, ...formatDiffSummary(diffSummary.value));
  }

  if (data.parallelDelegation?.groupCount > 0) {
    lines.push("", `## ${props.copy.runSummary.parallelDelegation}`,
      `- ${props.copy.runSummary.parallelDelegationSummary(data.parallelDelegation.groupCount, data.parallelDelegation.taskCount)}`,
      ...formatParallelDelegation(data.parallelDelegation),
    );
  }

  lines.push("", `## ${props.copy.runSummary.files}`, ...formatFileChanges(data.fileChanges));

  if (data.nextAction) {
    lines.push("", `## ${props.copy.runSummary.nextAction}`, `- ${data.nextAction}`);
  }

  if (data.warnings.length) {
    lines.push("", `## ${props.copy.runSummary.warnings}`, ...data.warnings.map((warning) => `- ${warning}`));
  }

  return `${lines.join("\n").trim()}\n`;
}

function formatTools(tools) {
  if (!tools.length) {
    return [`- ${props.copy.runSummary.noTools}`];
  }
  return tools.map((tool) => `- ${tool.name} x${tool.count}`);
}

function formatFileChanges(fileChanges) {
  if (!fileChanges.length) {
    return [`- ${props.copy.runSummary.noFiles}`];
  }

  return fileChanges.flatMap((change) => {
    const lines = [
      `### ${change.path}`,
      `- ${props.copy.runFileInspector.action}: ${change.action || props.copy.runFileInspector.unknown}`,
      `- ${props.copy.runFileInspector.tool}: ${change.toolName || props.copy.runFileInspector.unknown}`,
    ];
    if (change.diff) {
      lines.push("", "```diff", change.diff.trimEnd(), "```");
    } else {
      lines.push(`- ${props.copy.runSummary.noDiff}`);
    }
    return lines;
  });
}

function formatDiffSummary(diff) {
  const actions = Object.entries(diff.actions || {});
  const lines = [
    `- ${props.copy.runSummary.changedFiles}: ${diff.changedFiles}`,
    `- ${props.copy.runSummary.changes}: ${diff.changeCount}`,
    `- ${props.copy.runSummary.additions}: ${diff.additions}`,
    `- ${props.copy.runSummary.deletions}: ${diff.deletions}`,
  ];
  if (actions.length) {
    lines.push(`- ${props.copy.runSummary.actions}: ${actions.map(([action, count]) => `${action} x${count}`).join(", ")}`);
  }
  if (diff.paths.length) {
    lines.push(`- ${props.copy.runSummary.paths}: ${diff.paths.join(", ")}`);
  }
  return lines;
}

function formatParallelDelegation(data) {
  return (data.groups || []).flatMap((group) => {
    const lines = [
      `### ${parallelGroupChip(group)}`,
    ];
    if (group.summary) {
      lines.push(`- ${group.summary}`);
    }
    for (const task of group.tasks || []) {
      const taskLabel = [task.promptType || props.copy.runSummary.parallelTaskFallback, task.taskId].filter(Boolean).join(" ");
      const taskDetail = task.summary || task.error || task.status;
      lines.push(`- ${taskLabel}: ${taskDetail}`);
    }
    return lines;
  });
}

function parallelGroupChip(group) {
  return `${parallelGroupLabel(group)} ${parallelStatusLabel(group.status)} ${group.completedCount}/${group.totalTasks || group.tasks.length}`;
}

function parallelGroupLabel(group) {
  return props.copy.runSummary.parallelGroup(shortGroupId(group.groupId));
}

function parallelStatusLabel(status) {
  const labels = props.copy.runSummary.parallelStatusLabels || {};
  return labels[status] || status;
}

function shortGroupId(groupId) {
  const normalized = String(groupId || "").replace(/^fanout_/, "");
  return normalized.length > 8 ? normalized.slice(0, 8) : normalized || "group";
}

function downloadReport() {
  if (!summary.value) {
    return;
  }
  const report = buildRunReport();
  const blob = new Blob([report], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `opensprite-run-${safeFileName(props.run.runId)}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function safeFileName(value) {
  const normalized = String(value || "run").replace(/^run[_-]?/, "");
  return normalized.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64) || "run";
}
</script>
