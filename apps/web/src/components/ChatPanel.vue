<template>
  <main class="chat-panel">
    <header class="topbar">
      <div class="topbar__title">
        <strong>{{ copy.chat.title }}</strong>
        <span>{{ sessionMeta }}</span>
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
          v-if="entries.length === 0 && messages.length === 0"
          :copy="copy"
          :prompts="prompts"
          @apply-prompt="$emit('apply-prompt', $event)"
        />

        <MessageList :copy="copy" :entries="entries" :messages="messages" :display-name="displayName" />

        <PermissionPanel
          :copy="copy"
          :state="permissionState"
          :requests="permissionRequests"
          @resolve-permission="forwardPermissionResolution"
        />
      </div>
    </section>

    <ChatComposer
      :copy="copy"
      :model-value="messageText"
      :set-input-ref="setMessageInputRef"
      :disabled="sendDisabled"
      :read-only="composerReadOnly"
      :runtime-hint="runtimeHint"
      :command-hints="commandHints"
      @update:model-value="$emit('update-message-text', $event)"
      @input="$emit('composer-input')"
      @keydown="$emit('composer-keydown', $event)"
      @submit="$emit('submit-message', $event)"
      @apply-command-hint="$emit('apply-command-hint', $event)"
    />

  </main>
</template>

<script setup>
import ChatComposer from "./ChatComposer.vue";
import EmptyState from "./EmptyState.vue";
import MessageList from "./MessageList.vue";
import PermissionPanel from "./PermissionPanel.vue";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  prompts: {
    type: Array,
    required: true,
  },
  entries: {
    type: Array,
    required: true,
  },
  messages: {
    type: Array,
    required: true,
  },
  permissionState: {
    type: Object,
    required: true,
  },
  permissionRequests: {
    type: Array,
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
  commandHints: {
    type: Array,
    default: () => [],
  },
  displayName: {
    type: String,
    required: true,
  },
  messageText: {
    type: String,
    required: true,
  },
  sendDisabled: {
    type: Boolean,
    required: true,
  },
  composerReadOnly: {
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

const emit = defineEmits([
  "apply-prompt",
  "update-message-text",
  "composer-input",
  "composer-keydown",
  "submit-message",
  "apply-command-hint",
  "resolve-permission",
]);

function forwardPermissionResolution(request, decision) {
  emit("resolve-permission", request, decision);
}

</script>
