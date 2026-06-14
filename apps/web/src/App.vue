<template>
  <div
    class="app-shell"
    :class="{
      'app-shell--sidebar-collapsed': sidebarCollapsed,
      'app-shell--trace-collapsed': traceInspectorCollapsed,
    }"
    :style="appShellStyle"
  >
    <button
      class="mobile-nav-toggle"
      type="button"
      aria-controls="sidebar"
      :aria-expanded="String(sidebarOpen)"
      @click="toggleSidebar"
    >
      {{ sidebarOpen ? "關閉" : copy.app.menu }}
    </button>
    <button
      v-if="sidebarOpen"
      class="mobile-nav-backdrop"
      type="button"
      aria-label="Close menu"
      @click="toggleSidebar"
    ></button>

    <SidebarNav
      :copy="copy"
      :state="state"
      :sessions="sidebarSessions"
      :session-total="sidebarSessionTotal"
      :session-channel-filter="sessionChannelFilter"
      :show-hidden-sessions="showHiddenSessions"
      :collapsed="sidebarCollapsed"
      :get-session-display-id="getSessionDisplayId"
      :get-session-title="getSessionTitle"
      @create-new-chat="createNewChat"
      @delete-sessions="deleteSessions"
      @set-active-session="setActiveSession"
      @set-session-channel-filter="setSessionChannelFilter"
      @set-show-hidden-sessions="setShowHiddenSessions"
      @begin-sidebar-resize="beginSidebarResize"
      @toggle-sidebar-collapsed="toggleSidebarCollapsed"
      @open-settings="openSettings()"
    />

    <ChatPanel
      :copy="copy"
      :prompts="prompts"
      :entries="currentEntries"
      :messages="currentMessages"
      :notice="state.notice"
      :session-meta="sessionMeta"
      :runtime-hint="composerHint"
      :command-hints="commandHints"
      :display-name="state.displayName"
      :message-text="messageText"
      :composer-read-only="currentSessionReadOnly"
      :send-disabled="sendDisabled"
      :set-message-input-ref="setMessageInputRef"
      :set-message-stage-ref="setMessageStageRef"
      @apply-prompt="applyPrompt"
      @update-message-text="setMessageText"
      @composer-input="resizeComposer"
      @composer-keydown="handleComposerKeydown"
      @submit-message="submitMessage"
      @apply-command-hint="applyCommandHint"
    />

    <aside
      class="trace-sidebar"
      :data-collapsed="traceInspectorCollapsed"
      aria-label="Run trace inspector"
    >
      <button
        v-show="!traceInspectorCollapsed"
        class="trace-sidebar__resize"
        type="button"
        aria-label="Resize trace inspector"
        title="Drag to resize trace inspector"
        @pointerdown="beginTraceResize"
      ></button>
      <div class="trace-sidebar__rail">
        <button
          class="trace-sidebar__toggle"
          type="button"
          :aria-expanded="String(!traceInspectorCollapsed)"
          :aria-label="traceInspectorCollapsed ? 'Open trace panel' : 'Close trace panel'"
          :title="traceInspectorCollapsed ? 'Open trace panel' : 'Close trace panel'"
          @click="toggleTraceInspectorCollapsed"
        >
          <strong>{{ traceInspectorCollapsed ? 'Trace' : '關閉' }}</strong>
          <span v-if="traceInspectorCollapsed" aria-hidden="true">Open</span>
        </button>
      </div>

      <div v-show="!traceInspectorCollapsed" class="trace-sidebar__body">
        <WorkStateCard
          v-if="state.showWorkState && currentWorkState"
          :copy="copy"
          :work-state="currentWorkState"
          @resume-follow-up="resumeFollowUp"
          @run-verification="runVerification"
        />

        <RunDetailsPanel
          :copy="copy"
          :runs="currentRuns"
          :runs-loading="currentRunsLoading"
          :runs-error="currentRunsError"
          :current-run="currentRun"
          :run-timeline="currentRunTimeline"
          :run-summary="currentRunSummary"
          :show-run-history="state.showRunHistory"
          :show-run-timeline="state.showRunTimeline"
          :show-run-summary="state.showRunSummary"
          :show-run-trace="state.showRunTrace"
          @select-run="selectRun"
          @cancel-run="cancelRun"
          @cleanup-worktree="cleanupWorktreeSandbox"
          @resume-follow-up="resumeFollowUp"
          @revert-file-change="revertRunFileChange"
        />
      </div>
    </aside>
  </div>

  <section v-if="state.authRequired" class="auth-gate" aria-labelledby="authGateTitle">
    <form class="auth-gate__card" @submit.prevent="submitAccessToken">
      <span class="auth-gate__mark" aria-hidden="true">OS</span>
      <h1 id="authGateTitle">{{ copy.auth.title }}</h1>
      <p>{{ copy.auth.description }}</p>
      <label>
        <span>{{ copy.auth.tokenLabel }}</span>
        <input v-model="settingsForm.accessToken" type="password" autocomplete="current-password" spellcheck="false" autofocus />
      </label>
      <p v-if="state.authError" class="auth-gate__error">{{ state.authError }}</p>
      <div class="auth-gate__actions">
        <button class="primary-button" type="submit">{{ copy.auth.submit }}</button>
        <button class="secondary-button" type="button" @click="openSettings('general')">{{ copy.auth.settings }}</button>
      </div>
    </form>
  </section>

  <SettingsModal
    :copy="copy"
    :open="settingsOpen"
    :section="settingsSection"
    :title="settingsTitle"
    :form="settingsForm"
    :settings-state="settingsState"
    :web-session-count="webSessionCount"
    :connection-state="state.connectionState"
    @close="closeSettings"
    @select-section="selectSettingsSection"
    @save-connection-settings="saveConnectionSettings"
    @toggle-connection="toggleSettingsConnection"
    @check-update="loadUpdateStatus"
    @run-update="runUpdate"
    @begin-channel-connect="beginChannelConnect"
    @cancel-channel-connect="cancelChannelConnect"
    @save-channel-connection="saveChannelConnection"
    @disconnect-channel="disconnectChannel"
    @begin-provider-connect="beginProviderConnect"
    @connect-oauth-provider="connectOAuthProvider"
    @cancel-provider-connect="cancelProviderConnect"
    @save-provider-connection="saveProviderConnection"
    @disconnect-provider="disconnectProvider"
    @set-provider-credential="setProviderCredential"
    @delete-credential="deleteCredential"
    @refresh-codex-auth="loadCodexAuthStatus"
    @start-codex-auth-login="startCodexAuthLogin"
    @logout-codex-auth="logoutCodexAuth"
    @refresh-copilot-auth="loadCopilotAuthStatus"
    @start-copilot-auth-login="startCopilotAuthLogin"
    @logout-copilot-auth="logoutCopilotAuth"
    @select-model="selectModel"
    @save-log-settings="saveLogSettings"
    @save-media-model="saveMediaModel"
    @begin-mcp-create="beginMcpCreate"
    @save-mcp-server="saveMcpServer"
    @edit-mcp-server="beginMcpEdit"
    @cancel-mcp-edit="cancelMcpEdit"
    @remove-mcp-server="removeMcpServer"
    @reload-mcp-settings="reloadMcpSettings"
    @toggle-mcp-advanced="toggleMcpAdvanced"
    @toggle-mcp-json="toggleMcpJsonInput"
    @toggle-mcp-tool-group="toggleMcpToolGroup"
    @apply-mcp-json="applyMcpJson"
    @save-schedule-settings="saveScheduleSettings"
    @save-network-settings="saveNetworkSettings"
    @load-search-searxng-options="loadSearxngOptions"
    @save-search-settings="saveSearchSettings"
    @save-browser-settings="saveBrowserSettings"
    @run-browser-test="runBrowserTest"
    @run-browser-doctor="runBrowserDoctor"
    @run-browser-install="runBrowserInstall"
    @clear-web-sessions="clearWebSessions"
    @begin-cron-job-create="beginCronJobCreate"
    @save-cron-job="saveCronJob"
    @edit-cron-job="beginCronJobEdit"
    @cancel-cron-job-edit="cancelCronJobEdit"
    @cron-job-action="runCronJobAction"
  />

  <section
    v-if="confirmDialog.open"
    class="confirm-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby="confirmDialogTitle"
  >
    <button
      class="confirm-dialog__backdrop"
      type="button"
      :aria-label="confirmDialog.cancelLabel"
      :disabled="confirmDialog.busy"
      @click="cancelConfirmDialog"
    ></button>
    <div class="confirm-dialog__panel">
      <span class="confirm-dialog__eyebrow">{{ confirmDialog.eyebrow }}</span>
      <h2 id="confirmDialogTitle">{{ confirmDialog.title }}</h2>
      <p>{{ confirmDialog.message }}</p>
      <p v-if="confirmDialog.detail" class="confirm-dialog__detail">
        {{ confirmDialog.detail }}
      </p>
      <div class="confirm-dialog__actions">
        <button class="secondary-button" type="button" :disabled="confirmDialog.busy" @click="cancelConfirmDialog">
          {{ confirmDialog.cancelLabel }}
        </button>
        <button class="secondary-button secondary-button--danger" type="button" :disabled="confirmDialog.busy" @click="confirmDialogAction">
          {{ confirmDialog.confirmLabel }}
        </button>
      </div>
    </div>
  </section>

  <ToastStack :copy="copy" :toasts="toasts" @dismiss-toast="dismissToast" />
</template>

<script setup>
import { computed, ref } from "vue";
import ChatPanel from "./components/ChatPanel.vue";
import RunDetailsPanel from "./components/RunDetailsPanel.vue";
import SettingsModal from "./components/SettingsModal.vue";
import SidebarNav from "./components/SidebarNav.vue";
import ToastStack from "./components/ToastStack.vue";
import WorkStateCard from "./components/WorkStateCard.vue";
import { useChatClient } from "./composables/useChatClient";

const {
  copy,
  prompts,
  state,
  sidebarSessions,
  sidebarSessionTotal,
  webSessionCount,
  sessionChannelFilter,
  showHiddenSessions,
  messageText,
  sidebarOpen,
  sidebarCollapsed,
  traceInspectorCollapsed,
  settingsOpen,
  settingsSection,
  settingsForm,
  settingsState,
  toasts,
  currentEntries,
  currentMessages,
  currentWorkState,
  currentRuns,
  currentRunsLoading,
  currentRunsError,
  currentRun,
  currentRunTimeline,
  currentRunSummary,
  settingsTitle,
  sessionMeta,
  composerHint,
  commandHints,
  currentSessionReadOnly,
  sendDisabled,
  setMessageInputRef,
  setMessageStageRef,
  setMessageText,
  getSessionDisplayId,
  getSessionTitle,
  setActiveSession,
  setSessionChannelFilter,
  setShowHiddenSessions,
  selectRun,
  selectSettingsSection,
  openSettings,
  closeSettings,
  saveConnectionSettings,
  submitAccessToken,
  loadUpdateStatus,
  runUpdate,
  beginChannelConnect,
  cancelChannelConnect,
  saveChannelConnection,
  disconnectChannel,
  beginProviderConnect,
  connectOAuthProvider,
  cancelProviderConnect,
  saveProviderConnection,
  disconnectProvider,
  setProviderCredential,
  deleteCredential,
  loadCodexAuthStatus,
  loadCopilotAuthStatus,
  startCodexAuthLogin,
  logoutCodexAuth,
  startCopilotAuthLogin,
  logoutCopilotAuth,
  selectModel,
  saveLogSettings,
  saveMediaModel,
  beginMcpCreate,
  beginMcpEdit,
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
  loadSearxngOptions,
  saveSearchSettings,
  saveBrowserSettings,
  runBrowserTest,
  runBrowserDoctor,
  runBrowserInstall,
  beginCronJobCreate,
  beginCronJobEdit,
  cancelCronJobEdit,
  saveCronJob,
  runCronJobAction,
  toggleSidebar,
  toggleSidebarCollapsed,
  toggleTraceInspectorCollapsed,
  connectSocket,
  resizeComposer,
  createNewChat,
  deleteSessions: deleteSessionsNow,
  clearWebSessions: clearWebSessionsNow,
  cancelRun,
  revertRunFileChange,
  cleanupWorktreeSandbox,
  resumeFollowUp,
  runVerification,
  toggleSettingsConnection,
  submitMessage,
  handleComposerKeydown,
  applyPrompt,
  applyCommandHint,
  dismissToast,
} = useChatClient();

const TRACE_WIDTH_STORAGE_KEY = "opensprite:web:traceInspectorWidth";
const SIDEBAR_WIDTH_STORAGE_KEY = "opensprite:web:sidebarWidth";
const SIDEBAR_WIDTH_DEFAULT = 268;
const SIDEBAR_WIDTH_MIN = 220;
const SIDEBAR_WIDTH_MAX = 440;
const SIDEBAR_COLLAPSED_WIDTH = 52;
const TRACE_WIDTH_MIN = 440;
const TRACE_CHAT_MIN = 520;

const sidebarWidth = ref(readStoredSidebarWidth());
const traceInspectorWidth = ref(readStoredTraceWidth());
const appShellStyle = computed(() => ({
  "--sidebar-width": sidebarWidth.value ? `${sidebarWidth.value}px` : undefined,
  "--trace-sidebar-width": traceInspectorWidth.value ? `${traceInspectorWidth.value}px` : undefined,
}));

const confirmDialog = ref({
  open: false,
  eyebrow: "",
  title: "",
  message: "",
  detail: "",
  cancelLabel: "",
  confirmLabel: "",
  busy: false,
  action: null,
});

function openConfirmDialog({ eyebrow, title, message, detail, cancelLabel, confirmLabel, action }) {
  confirmDialog.value = {
    open: true,
    eyebrow,
    title,
    message,
    detail,
    cancelLabel,
    confirmLabel,
    busy: false,
    action,
  };
}

function readStoredTraceWidth() {
  try {
    const value = Number.parseInt(window.localStorage.getItem(TRACE_WIDTH_STORAGE_KEY) || "", 10);
    return Number.isFinite(value) ? clampTraceWidth(value) : 0;
  } catch {
    return 0;
  }
}

function readStoredSidebarWidth() {
  try {
    const value = Number.parseInt(window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY) || "", 10);
    return Number.isFinite(value) ? clampSidebarWidth(value) : SIDEBAR_WIDTH_DEFAULT;
  } catch {
    return SIDEBAR_WIDTH_DEFAULT;
  }
}

function clampSidebarWidth(width) {
  const viewportWidth = window.innerWidth || 0;
  const maxFromViewport = viewportWidth
    ? Math.max(SIDEBAR_WIDTH_MIN, viewportWidth - TRACE_CHAT_MIN - (traceInspectorCollapsed.value ? 0 : TRACE_WIDTH_MIN))
    : SIDEBAR_WIDTH_MAX;
  const maxWidth = Math.min(SIDEBAR_WIDTH_MAX, maxFromViewport);
  return Math.round(Math.min(Math.max(width, SIDEBAR_WIDTH_MIN), maxWidth));
}

function currentSidebarGutter() {
  return sidebarCollapsed.value ? SIDEBAR_COLLAPSED_WIDTH : sidebarWidth.value || SIDEBAR_WIDTH_DEFAULT;
}

function clampTraceWidth(width) {
  const viewportWidth = window.innerWidth || 0;
  const fallbackMax = Math.max(TRACE_WIDTH_MIN, Math.round(viewportWidth * 0.78));
  const maxWidth = viewportWidth
    ? Math.max(TRACE_WIDTH_MIN, viewportWidth - currentSidebarGutter() - TRACE_CHAT_MIN)
    : fallbackMax;
  return Math.round(Math.min(Math.max(width, TRACE_WIDTH_MIN), maxWidth));
}

function beginSidebarResize(event) {
  if (sidebarCollapsed.value || event.button !== 0) {
    return;
  }

  event.preventDefault();
  event.currentTarget.setPointerCapture?.(event.pointerId);

  const handlePointerMove = (moveEvent) => {
    sidebarWidth.value = clampSidebarWidth(moveEvent.clientX);
    if (!traceInspectorCollapsed.value && traceInspectorWidth.value) {
      traceInspectorWidth.value = clampTraceWidth(traceInspectorWidth.value);
    }
  };

  const stopResize = () => {
    window.removeEventListener("pointermove", handlePointerMove);
    window.removeEventListener("pointerup", stopResize);
    window.removeEventListener("pointercancel", stopResize);
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(sidebarWidth.value));
      if (traceInspectorWidth.value) {
        window.localStorage.setItem(TRACE_WIDTH_STORAGE_KEY, String(traceInspectorWidth.value));
      }
    } catch {
      // Ignore storage failures; resize still works for the active session.
    }
  };

  window.addEventListener("pointermove", handlePointerMove);
  window.addEventListener("pointerup", stopResize, { once: true });
  window.addEventListener("pointercancel", stopResize, { once: true });
}

function beginTraceResize(event) {
  if (traceInspectorCollapsed.value || event.button !== 0) {
    return;
  }

  event.preventDefault();
  event.currentTarget.setPointerCapture?.(event.pointerId);

  const handlePointerMove = (moveEvent) => {
    traceInspectorWidth.value = clampTraceWidth(window.innerWidth - moveEvent.clientX);
  };

  const stopResize = () => {
    window.removeEventListener("pointermove", handlePointerMove);
    window.removeEventListener("pointerup", stopResize);
    window.removeEventListener("pointercancel", stopResize);
    try {
      window.localStorage.setItem(TRACE_WIDTH_STORAGE_KEY, String(traceInspectorWidth.value));
    } catch {
      // Ignore storage failures; resize still works for the active session.
    }
  };

  window.addEventListener("pointermove", handlePointerMove);
  window.addEventListener("pointerup", stopResize, { once: true });
  window.addEventListener("pointercancel", stopResize, { once: true });
}

function closeConfirmDialog() {
  confirmDialog.value = {
    open: false,
    eyebrow: "",
    title: "",
    message: "",
    detail: "",
    cancelLabel: "",
    confirmLabel: "",
    busy: false,
    action: null,
  };
}

function cancelConfirmDialog() {
  if (confirmDialog.value.busy) {
    return;
  }
  closeConfirmDialog();
}

async function confirmDialogAction() {
  const action = confirmDialog.value.action;
  if (typeof action !== "function" || confirmDialog.value.busy) {
    return;
  }
  confirmDialog.value = { ...confirmDialog.value, busy: true };
  try {
    await action();
  } finally {
    closeConfirmDialog();
  }
}

function deleteSessions(sessions) {
  const targets = Array.isArray(sessions) ? sessions.filter(Boolean) : [];
  if (!targets.length) {
    return;
  }
  openConfirmDialog({
    eyebrow: copy.value.sidebar.deleteChat,
    title: copy.value.sidebar.confirmDeleteTitle,
    message: targets.length === 1
      ? copy.value.sidebar.confirmDeleteChat(getSessionTitle(targets[0]))
      : copy.value.sidebar.confirmDeleteChats(targets.length),
    detail: copy.value.sidebar.confirmDeleteDetail,
    cancelLabel: copy.value.sidebar.cancelDelete,
    confirmLabel: copy.value.sidebar.confirmDeleteAction,
    action: () => deleteSessionsNow(targets),
  });
}

function clearWebSessions() {
  openConfirmDialog({
    eyebrow: copy.value.settings.general.clearWebChats.action,
    title: copy.value.settings.general.clearWebChats.confirmTitle,
    message: copy.value.settings.general.clearWebChats.confirm,
    detail: copy.value.settings.general.clearWebChats.confirmDescription(webSessionCount.value || 0),
    cancelLabel: copy.value.sidebar.cancelDelete,
    confirmLabel: copy.value.settings.general.clearWebChats.confirmAction,
    action: () => clearWebSessionsNow(),
  });
}

</script>
