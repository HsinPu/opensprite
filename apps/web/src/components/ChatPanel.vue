<template>
  <main class="chat-panel">
    <header class="topbar">
      <div class="topbar__title">
        <strong>OpenSprite Chat</strong>
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
          :prompts="prompts"
          @apply-prompt="$emit('apply-prompt', $event)"
        />

        <MessageList :messages="messages" :display-name="displayName" />

        <RunTimeline
          v-if="runSummary"
          :summary="runSummary"
          :events="runTimeline"
        />

        <RunTraceViewer v-if="currentRun" :run="currentRun" @cancel-run="$emit('cancel-run', $event)" />
      </div>
    </section>

    <ChatComposer
      :model-value="messageText"
      :set-input-ref="setMessageInputRef"
      :disabled="sendDisabled"
      :runtime-hint="runtimeHint"
      @update:model-value="$emit('update-message-text', $event)"
      @input="$emit('composer-input')"
      @keydown="$emit('composer-keydown', $event)"
      @submit="$emit('submit-message', $event)"
    />
  </main>
</template>

<script setup>
import ChatComposer from "./ChatComposer.vue";
import EmptyState from "./EmptyState.vue";
import MessageList from "./MessageList.vue";
import RunTimeline from "./RunTimeline.vue";
import RunTraceViewer from "./RunTraceViewer.vue";

defineProps({
  prompts: {
    type: Array,
    required: true,
  },
  messages: {
    type: Array,
    required: true,
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
]);
</script>
