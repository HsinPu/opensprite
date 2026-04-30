<template>
  <main class="chat-panel">
    <header class="topbar">
      <div class="topbar__title">
        <strong>{{ copy.chat.title }}</strong>
        <span>{{ sessionMeta }}</span>
      </div>

      <div class="connection-card" aria-live="polite">
        <span class="status-dot" :class="statusDotClass"></span>
        <strong>{{ connectionLabel }}</strong>
        <button
          class="ghost-button"
          type="button"
          :disabled="connecting"
          @click="$emit('connect')"
        >
          {{ connectButtonLabel }}
        </button>
      </div>
    </header>

    <div
      v-show="notice.text"
      class="notice-banner"
      role="status"
      :data-tone="notice.tone || 'info'"
    >
      {{ notice.text }}
    </div>

    <section :ref="setMessageStageRef" class="message-stage" aria-live="polite">
      <div class="conversation-wrap">
        <EmptyState
          v-if="messages.length === 0"
          :copy="copy"
          :prompts="prompts"
          @apply-prompt="$emit('apply-prompt', $event)"
        />

        <MessageList :copy="copy" :messages="messages" :display-name="displayName" />

        <WorkStateCard v-if="workState" :copy="copy" :work-state="workState" />

        <section v-if="runs.length > 1 || runsLoading || runsError" class="run-history" aria-live="polite">
          <div class="run-history__title">
            <span>{{ copy.runHistory.title }}</span>
            <small v-if="runsLoading">{{ copy.runHistory.loading }}</small>
            <small v-else-if="runsError">{{ copy.runHistory.unavailable }}</small>
          </div>

          <label v-if="runs.length" class="run-history__select">
            <span class="sr-only">{{ copy.runHistory.select }}</span>
            <select :value="currentRun?.runId || ''" @change="$emit('select-run', $event.target.value)">
              <option v-for="(run, index) in runs" :key="run.runId" :value="run.runId">
                {{ runOptionLabel(run, index) }}
              </option>
            </select>
          </label>
        </section>

        <RunSummaryCard
          v-if="currentRun && (currentRun.summary || currentRun.summaryLoading || currentRun.summaryError)"
          :copy="copy"
          :run="currentRun"
          @inspect-file="selectedFileChange = $event"
        />

        <RunTimeline
          v-if="showRunTimeline && runSummary"
          :copy="copy"
          :summary="runSummary"
          :events="runTimeline"
        />

        <RunTraceViewer
          v-if="showRunTrace && currentRun"
          :copy="copy"
          :run="currentRun"
          @cancel-run="$emit('cancel-run', $event)"
          @inspect-file="selectedFileChange = $event"
        />
      </div>
    </section>

    <ChatComposer
      :copy="copy"
      :model-value="messageText"
      :set-input-ref="setMessageInputRef"
      :disabled="sendDisabled"
      :runtime-hint="runtimeHint"
      @update:model-value="$emit('update-message-text', $event)"
      @input="$emit('composer-input')"
      @keydown="$emit('composer-keydown', $event)"
      @submit="$emit('submit-message', $event)"
    />

    <RunFileChangeDrawer
      v-if="currentRun && selectedFileChange"
      :copy="copy"
      :run="currentRun"
      :change="selectedFileChange"
      @close="selectedFileChange = null"
    />
  </main>
</template>

<script setup>
import { ref, watch } from "vue";

import ChatComposer from "./ChatComposer.vue";
import EmptyState from "./EmptyState.vue";
import MessageList from "./MessageList.vue";
import RunFileChangeDrawer from "./RunFileChangeDrawer.vue";
import RunSummaryCard from "./RunSummaryCard.vue";
import RunTimeline from "./RunTimeline.vue";
import RunTraceViewer from "./RunTraceViewer.vue";
import WorkStateCard from "./WorkStateCard.vue";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  prompts: {
    type: Array,
    required: true,
  },
  messages: {
    type: Array,
    required: true,
  },
  workState: {
    type: Object,
    default: null,
  },
  runs: {
    type: Array,
    required: true,
  },
  runsLoading: {
    type: Boolean,
    required: true,
  },
  runsError: {
    type: String,
    default: "",
  },
  currentRun: {
    type: Object,
    default: null,
  },
  runTimeline: {
    type: Array,
    required: true,
  },
  runSummary: {
    type: Object,
    default: null,
  },
  showRunTimeline: {
    type: Boolean,
    required: true,
  },
  showRunTrace: {
    type: Boolean,
    required: true,
  },
  notice: {
    type: Object,
    required: true,
  },
  sessionMeta: {
    type: String,
    required: true,
  },
  runtimeHint: {
    type: String,
    required: true,
  },
  displayName: {
    type: String,
    required: true,
  },
  messageText: {
    type: String,
    required: true,
  },
  connectionLabel: {
    type: String,
    required: true,
  },
  connectButtonLabel: {
    type: String,
    required: true,
  },
  statusDotClass: {
    type: Object,
    required: true,
  },
  sendDisabled: {
    type: Boolean,
    required: true,
  },
  connecting: {
    type: Boolean,
    required: true,
  },
  setMessageInputRef: {
    type: Function,
    required: true,
  },
  setMessageStageRef: {
    type: Function,
    required: true,
  },
});

defineEmits([
  "connect",
  "apply-prompt",
  "update-message-text",
  "composer-input",
  "composer-keydown",
  "submit-message",
  "cancel-run",
  "select-run",
]);

const selectedFileChange = ref(null);

watch(
  () => props.currentRun?.runId,
  () => {
    selectedFileChange.value = null;
  },
);

function shortRunId(runId) {
  const normalized = String(runId || "run").replace(/^run[_-]?/, "");
  return normalized.length > 8 ? normalized.slice(0, 8) : normalized;
}

function runOptionLabel(run, index) {
  const statusLabel = props.copy.run.statusLabels[run.status] || run.status;
  const prefix = index === 0 ? props.copy.runHistory.latest : `#${index + 1}`;
  return `${prefix} · Run ${shortRunId(run.runId)} · ${statusLabel}`;
}
</script>
