<template>
  <p v-if="state.error" class="settings-inline-status settings-inline-status--error">{{ state.error }}</p>

  <h3>{{ copy.curator.title }}</h3>
  <div class="settings-card">
    <div class="settings-row">
      <div>
        <strong>{{ copy.curator.state }}</strong>
        <span>{{ copy.curator.eyebrow }}</span>
      </div>
      <span class="provider-row__badge">{{ status?.state || copy.curator.unknown }}</span>
    </div>

    <div class="settings-row">
      <div>
        <strong>{{ copy.curator.paused }}</strong>
        <span>{{ status?.paused ? copy.curator.yes : copy.curator.no }}</span>
      </div>
      <button class="ghost-button" type="button" :disabled="state.loading" @click="$emit('refresh-curator')">
        {{ state.loading ? copy.curator.loading : copy.curator.refresh }}
      </button>
    </div>

    <div class="settings-row">
      <div>
        <strong>{{ copy.curator.currentJob }}</strong>
        <span :title="currentCuratorJobLabel">{{ currentCuratorJobLabel }}</span>
      </div>
      <span>{{ copy.curator.runCount }}: {{ status?.run_count || 0 }}</span>
    </div>

    <div class="settings-row">
      <div>
        <strong>{{ copy.curator.lastRun }}</strong>
        <span>{{ status?.last_run_at || copy.curator.never }}</span>
      </div>
      <span :title="lastCuratorChangedLabel">{{ lastCuratorChangedLabel }}</span>
    </div>
  </div>

  <h3>{{ copy.curator.scope }}</h3>
  <div class="settings-card settings-card--form">
    <label class="settings-row settings-row--field">
      <div>
        <strong>{{ copy.curator.scope }}</strong>
        <span>{{ copy.curator.lastJobs }}: {{ lastCuratorJobsLabel }}</span>
      </div>
      <select v-model="selectedCuratorScope" :disabled="Boolean(state.action || state.loading)">
        <option v-for="option in curatorScopeOptions" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
    </label>

    <div class="settings-row">
      <div>
        <strong>{{ copy.curator.run }}</strong>
        <span>{{ status?.last_run_summary || copy.curator.noSummary }}</span>
      </div>
      <button
        class="primary-button"
        type="button"
        :disabled="actionsDisabled"
        @click="$emit('run-curator-action', { action: 'run', scope: selectedCuratorScope === 'all' ? '' : selectedCuratorScope })"
      >
        {{ state.action === 'run' ? copy.curator.running : copy.curator.run }}
      </button>
    </div>

    <div class="settings-row">
      <div>
        <strong>{{ copy.curator.pause }} / {{ copy.curator.resume }}</strong>
        <span>{{ status?.paused ? copy.curator.paused : copy.curator.state }}</span>
      </div>
      <div class="mcp-editor__toolbar">
        <button class="secondary-button" type="button" :disabled="actionsDisabled || status?.paused" @click="$emit('run-curator-action', 'pause')">
          {{ state.action === 'pause' ? copy.curator.pausing : copy.curator.pause }}
        </button>
        <button class="secondary-button" type="button" :disabled="actionsDisabled || !status?.paused" @click="$emit('run-curator-action', 'resume')">
          {{ state.action === 'resume' ? copy.curator.resuming : copy.curator.resume }}
        </button>
      </div>
    </div>
  </div>

  <p v-if="status?.last_error" class="settings-inline-status settings-inline-status--error">
    {{ copy.curator.lastError }}: {{ status.last_error }}
  </p>

  <h3>{{ copy.curator.historyTitle }}</h3>
  <div class="settings-card provider-card" aria-live="polite">
    <div v-if="state.historyLoading" class="provider-row provider-row--empty">
      <span>{{ copy.curator.historyLoading }}</span>
    </div>

    <div v-else-if="curatorHistoryEntries.length === 0" class="provider-row provider-row--empty">
      <span>{{ state.historyError || copy.curator.historyEmpty }}</span>
    </div>

    <template v-else>
      <div v-for="entry in curatorHistoryEntries" :key="entry.key" class="provider-row provider-row--stacked">
        <div class="provider-row__content">
          <div class="provider-row__title">
            <strong>{{ entry.runAt }}</strong>
            <span class="provider-row__badge">{{ entry.statusLabel }}</span>
          </div>
          <span>{{ entry.summary }}</span>
          <small>{{ copy.curator.lastJobs }}: {{ entry.jobs }}</small>
          <small v-if="entry.changed">{{ copy.curator.lastChanged }}: {{ entry.changed }}</small>
          <small v-if="entry.error">{{ copy.curator.lastError }}: {{ entry.error }}</small>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  state: {
    type: Object,
    required: true,
  },
  status: {
    type: Object,
    default: null,
  },
});

defineEmits(["refresh-curator", "run-curator-action"]);

const selectedCuratorScope = ref("all");

const actionsDisabled = computed(() => {
  return Boolean(props.state.action || props.state.loading || props.state.error || !props.status);
});

const curatorScopeOptions = computed(() => {
  const labels = props.copy.curator.scopes || {};
  return [
    { value: "all", label: labels.all || "all" },
    { value: "maintenance", label: labels.maintenance || "maintenance" },
    { value: "skills", label: labels.skills || "skills" },
    { value: "memory", label: labels.memory || "memory" },
    { value: "recent_summary", label: labels.recent_summary || "recent_summary" },
    { value: "user_profile", label: labels.user_profile || "user_profile" },
    { value: "active_task", label: labels.active_task || "active_task" },
  ];
});

const currentCuratorJobLabel = computed(() => {
  return String(props.status?.current_job_label || props.status?.current_job || "").trim() || props.copy.curator.none;
});

const lastCuratorJobsLabel = computed(() => {
  const jobs = Array.isArray(props.status?.last_run_jobs) ? props.status.last_run_jobs : [];
  return jobs.length ? jobs.join(", ") : props.copy.curator.none;
});

const lastCuratorChangedLabel = computed(() => {
  const changed = Array.isArray(props.status?.last_run_changed) ? props.status.last_run_changed : [];
  return changed.length ? changed.join(", ") : props.copy.curator.none;
});

const curatorHistoryEntries = computed(() => {
  const entries = Array.isArray(props.state.history) ? props.state.history : [];
  return entries.map((entry, index) => ({
    key: `${entry.run_id || "history"}:${entry.run_at || index}:${index}`,
    runAt: formatCuratorHistoryTime(entry.run_at),
    statusLabel: props.copy.run.statusLabels[String(entry.status || "").trim()] || String(entry.status || props.copy.curator.unknown),
    summary: String(entry.summary || "").trim() || props.copy.curator.noSummary,
    jobs: Array.isArray(entry.jobs) && entry.jobs.length ? entry.jobs.join(", ") : props.copy.curator.none,
    changed: Array.isArray(entry.changed) && entry.changed.length ? entry.changed.join(", ") : "",
    error: String(entry.error || "").trim(),
  }));
});

function formatCuratorHistoryTime(value) {
  const normalized = String(value || "").trim();
  const date = new Date(normalized);
  if (!normalized || Number.isNaN(date.getTime())) {
    return normalized || props.copy.curator.unknown;
  }
  return date.toLocaleString();
}
</script>
