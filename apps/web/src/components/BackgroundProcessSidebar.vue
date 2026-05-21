<template>
  <section
    class="background-process-sidebar"
    :class="{
      'background-process-sidebar--collapsed': collapsed,
      'background-process-sidebar--section-collapsed': sectionCollapsed,
    }"
  >
    <button
      v-if="collapsed"
      class="background-process-sidebar__collapsed-button"
      type="button"
      :title="copy.sidebar.backgroundProcesses"
      @click="$emit('refresh')"
    >
      <span aria-hidden="true">BG</span>
      <strong v-if="processes.length">{{ processes.length }}</strong>
    </button>

    <template v-else>
      <div class="sidebar__section-head background-process-sidebar__head">
        <button
          class="background-process-sidebar__toggle"
          type="button"
          :aria-expanded="String(!sectionCollapsed)"
          @click="sectionCollapsed = !sectionCollapsed"
        >
          <span>{{ copy.sidebar.backgroundProcesses }}</span>
          <strong>{{ processes.length }}</strong>
          <small aria-hidden="true">{{ sectionCollapsed ? "+" : "-" }}</small>
        </button>
        <button
          v-if="!sectionCollapsed"
          class="background-process-sidebar__refresh"
          type="button"
          :disabled="loading"
          :aria-label="copy.sidebar.backgroundProcessesRefresh"
          :title="loading ? copy.sidebar.backgroundProcessesLoading : copy.sidebar.backgroundProcessesRefresh"
          @click="$emit('refresh')"
        >
          <svg
            :class="{ 'background-process-sidebar__refresh-icon--loading': loading }"
            class="background-process-sidebar__refresh-icon"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path d="M20 12a8 8 0 1 1-2.34-5.66" />
            <path d="M20 4v5h-5" />
          </svg>
        </button>
      </div>

      <template v-if="!sectionCollapsed">
        <div class="background-process-sidebar__summary">
          <span>{{ copy.sidebar.backgroundProcessesCurrent(activeCount) }}</span>
          <span>{{ copy.sidebar.backgroundProcessesAll(processes.length) }}</span>
        </div>

        <p v-if="error" class="background-process-sidebar__notice">{{ error }}</p>
        <p v-else-if="!processes.length" class="background-process-sidebar__notice">
          {{ copy.sidebar.backgroundProcessesEmpty }}
        </p>

        <div v-else class="background-process-sidebar__list">
          <article
            v-for="process in processes"
            :key="process.processSessionId"
            class="background-process-card"
            :class="{
              'background-process-card--active-session': process.ownerSessionId === activeSessionId,
              [`background-process-card--${process.state}`]: true,
            }"
          >
            <button
              class="background-process-card__main"
              type="button"
              :title="process.command || process.processSessionId"
              @click="$emit('select-session', process)"
            >
              <span class="background-process-card__status">{{ statusLabel(process.state) }}</span>
              <strong>{{ process.command || copy.sidebar.backgroundProcessNoCommand }}</strong>
              <small>{{ process.cwd || process.processSessionId }}</small>
            </button>
            <div class="background-process-card__meta">
              <span v-if="process.pid">{{ copy.sidebar.backgroundProcessPid(process.pid) }}</span>
              <button v-if="process.ownerRunId" type="button" @click="$emit('select-run', process)">
                {{ copy.sidebar.backgroundProcessRun(shortId(process.ownerRunId)) }}
              </button>
              <span>{{ copy.sidebar.backgroundProcessUpdated(formatTime(process.updatedAt)) }}</span>
            </div>
          </article>
        </div>
      </template>
    </template>
  </section>
</template>

<script setup>
import { computed, ref } from "vue";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  processes: {
    type: Array,
    required: true,
  },
  loading: {
    type: Boolean,
    required: true,
  },
  error: {
    type: String,
    required: true,
  },
  collapsed: {
    type: Boolean,
    required: true,
  },
  activeSessionId: {
    type: String,
    default: "",
  },
});

defineEmits(["select-session", "select-run", "refresh"]);

const sectionCollapsed = ref(true);
const activeCount = computed(() => props.processes.filter((process) => process.ownerSessionId === props.activeSessionId).length);

function statusLabel(state) {
  return props.copy.sidebar.backgroundProcessStatusLabels[state] || props.copy.sidebar.backgroundProcessStatusLabels.unknown;
}

function shortId(value) {
  const normalized = String(value || "").trim();
  return normalized.length > 8 ? normalized.slice(0, 8) : normalized;
}

function formatTime(timestamp) {
  const date = new Date(Number(timestamp || 0));
  if (Number.isNaN(date.getTime())) {
    return "--:--";
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
</script>
