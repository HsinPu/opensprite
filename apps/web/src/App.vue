<template>
  <div class="app-shell">
    <button
      class="mobile-nav-toggle"
      type="button"
      aria-controls="sidebar"
      :aria-expanded="String(sidebarOpen)"
      @click="toggleSidebar"
    >
      Menu
    </button>

    <SidebarNav
      :state="state"
      :get-session-display-id="getSessionDisplayId"
      @create-new-chat="createNewChat"
      @set-active-session="setActiveSession"
      @open-settings="openSettings()"
    />

    <ChatPanel
      :prompts="prompts"
      :messages="currentMessages"
      :current-run="currentRun"
      :run-timeline="currentRunTimeline"
      :run-summary="currentRunSummary"
      :show-run-timeline="state.showRunTimeline"
      :show-run-trace="state.showRunTrace"
      :notice="state.notice"
      :session-meta="sessionMeta"
      :runtime-hint="runtimeHint"
      :display-name="state.displayName"
      :message-text="messageText"
      :connection-label="connectionLabel"
      :connect-button-label="connectButtonLabel"
      :status-dot-class="statusDotClass"
      :send-disabled="sendDisabled"
      :connecting="state.connectionState === 'connecting'"
      :set-message-input-ref="setMessageInputRef"
      :set-message-stage-ref="setMessageStageRef"
      @connect="connectSocket"
      @apply-prompt="applyPrompt"
      @update-message-text="setMessageText"
      @composer-input="resizeComposer"
      @composer-keydown="handleComposerKeydown"
      @submit-message="submitMessage"
      @cancel-run="cancelRun"
    />
  </div>

  <SettingsModal
    :open="settingsOpen"
    :section="settingsSection"
    :title="settingsTitle"
    :form="settingsForm"
    :settings-state="settingsState"
    :connection-state="state.connectionState"
    @close="closeSettings"
    @select-section="selectSettingsSection"
    @toggle-connection="toggleSettingsConnection"
    @begin-channel-connect="beginChannelConnect"
    @cancel-channel-connect="cancelChannelConnect"
    @save-channel-connection="saveChannelConnection"
    @disconnect-channel="disconnectChannel"
    @begin-provider-connect="beginProviderConnect"
    @cancel-provider-connect="cancelProviderConnect"
    @save-provider-connection="saveProviderConnection"
    @disconnect-provider="disconnectProvider"
    @select-model="selectModel"
  />
</template>

<script setup>
import ChatPanel from "./components/ChatPanel.vue";
import SettingsModal from "./components/SettingsModal.vue";
import SidebarNav from "./components/SidebarNav.vue";
import { useChatClient } from "./composables/useChatClient";

const {
  prompts,
  state,
  messageText,
  sidebarOpen,
  settingsOpen,
  settingsSection,
  settingsForm,
  settingsState,
  currentMessages,
  currentRun,
  currentRunTimeline,
  currentRunSummary,
  settingsTitle,
  sessionMeta,
  runtimeHint,
  connectionLabel,
  connectButtonLabel,
  statusDotClass,
  sendDisabled,
  setMessageInputRef,
  setMessageStageRef,
  setMessageText,
  getSessionDisplayId,
  setActiveSession,
  selectSettingsSection,
  openSettings,
  closeSettings,
  beginChannelConnect,
  cancelChannelConnect,
  saveChannelConnection,
  disconnectChannel,
  beginProviderConnect,
  cancelProviderConnect,
  saveProviderConnection,
  disconnectProvider,
  selectModel,
  toggleSidebar,
  connectSocket,
  resizeComposer,
  createNewChat,
  cancelRun,
  toggleSettingsConnection,
  submitMessage,
  handleComposerKeydown,
  applyPrompt,
} = useChatClient();
</script>
