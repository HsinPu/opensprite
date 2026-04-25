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

    <aside class="sidebar" id="sidebar" aria-label="Chat navigation">
      <div class="sidebar__top">
        <div class="brand-row">
          <div class="brand-mark" aria-hidden="true">OS</div>
          <div>
            <strong>OpenSprite</strong>
            <span>Local assistant</span>
          </div>
        </div>

        <button class="new-chat-button" type="button" @click="createNewChat">
          <span aria-hidden="true">+</span>
          New chat
        </button>

        <section class="sidebar__section">
          <div class="sidebar__section-head">
            <span>Chats</span>
            <small>{{ state.sessions.length }}</small>
          </div>
          <div class="session-list">
            <button
              v-for="session in state.sessions"
              :key="session.chatId"
              class="session-tile"
              :class="{ 'session-tile--active': session.chatId === state.activeChatId }"
              type="button"
              @click="setActiveSession(session.chatId)"
            >
              <strong>{{ session.title }}</strong>
              <span>{{ getSessionDisplayId(session) }}</span>
            </button>
          </div>
        </section>
      </div>

      <div class="sidebar__bottom">
        <button class="settings-button" type="button" @click="openSettings()">
          <span class="settings-button__avatar" aria-hidden="true">OS</span>
          <span>
            <strong>Settings</strong>
            <small>Preferences and server</small>
          </span>
        </button>
      </div>
    </aside>

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
            :disabled="state.connectionState === 'connecting'"
            @click="connectSocket"
          >
            {{ connectButtonLabel }}
          </button>
        </div>
      </header>

      <div
        v-show="state.notice.text"
        class="notice-banner"
        role="status"
        :data-tone="state.notice.tone || 'info'"
      >
        {{ state.notice.text }}
      </div>

      <section ref="messageStage" class="message-stage" aria-live="polite">
        <div class="conversation-wrap">
          <section v-if="currentMessages.length === 0" class="empty-state" aria-label="Start a chat">
            <div class="empty-state__mark" aria-hidden="true">OS</div>
            <h1>How can OpenSprite help?</h1>
            <p>Start a local conversation. The page connects to your gateway automatically.</p>
            <div class="prompt-grid">
              <button
                v-for="prompt in prompts"
                :key="prompt.title"
                class="prompt-card"
                type="button"
                @click="applyPrompt(prompt.text)"
              >
                <strong>{{ prompt.title }}</strong>
                <span>{{ prompt.description }}</span>
              </button>
            </div>
          </section>

          <div class="message-list">
            <article
              v-for="message in currentMessages"
              :key="message.id"
              class="message"
              :class="`message--${message.role}`"
            >
              <div class="message__avatar">{{ message.role === "user" ? "You" : "OS" }}</div>
              <div class="message__content">
                <div class="message__meta">
                  {{ message.meta || (message.role === "user" ? state.displayName : "OpenSprite") }}
                </div>
                <div class="message__bubble">{{ message.text }}</div>
              </div>
            </article>
          </div>

          <section
            v-if="currentRunSummary"
            class="run-timeline"
            :data-tone="currentRunSummary.tone"
            aria-live="polite"
          >
            <div class="run-timeline__header">
              <div>
                <span class="run-timeline__eyebrow">Run {{ currentRunSummary.shortId }}</span>
                <strong>{{ currentRunSummary.title }}</strong>
              </div>
              <span class="run-timeline__status">{{ currentRunSummary.statusLabel }}</span>
            </div>

            <ol class="run-timeline__list">
              <li
                v-for="event in currentRunTimeline"
                :key="event.id"
                class="run-timeline__item"
                :data-tone="event.tone"
              >
                <span class="run-timeline__dot" aria-hidden="true"></span>
                <div class="run-timeline__text">
                  <strong>{{ event.label }}</strong>
                  <span v-if="event.detail">{{ event.detail }}</span>
                </div>
                <time>{{ formatEventTime(event.createdAt) }}</time>
              </li>
            </ol>
          </section>
        </div>
      </section>

      <form class="composer" @submit="submitMessage">
        <label class="sr-only" for="messageInput">Message</label>
        <div class="composer__box">
          <textarea
            id="messageInput"
            ref="messageInput"
            v-model="messageText"
            rows="1"
            placeholder="Message OpenSprite"
            autocomplete="off"
            @input="resizeComposer"
            @keydown="handleComposerKeydown"
          ></textarea>
          <button class="send-button" type="submit" aria-label="Send message" :disabled="sendDisabled">
            Send
          </button>
        </div>
        <div class="composer__footer">
          <span>OpenSprite can make mistakes. Check important work.</span>
          <span>{{ runtimeHint }}</span>
        </div>
      </form>
    </main>
  </div>

  <div v-if="settingsOpen" class="settings-modal">
    <button
      class="settings-modal__backdrop"
      type="button"
      aria-label="Close settings"
      @click="closeSettings"
    ></button>

    <section class="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
      <aside class="settings-nav" aria-label="Settings sections">
        <div class="settings-nav__group">
          <p>桌面</p>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': settingsSection === 'general' }"
            type="button"
            @click="selectSettingsSection('general')"
          >
            <span aria-hidden="true">⌘</span>
            一般
          </button>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': settingsSection === 'shortcuts' }"
            type="button"
            @click="selectSettingsSection('shortcuts')"
          >
            <span aria-hidden="true">⌗</span>
            快速鍵
          </button>
        </div>

        <div class="settings-nav__group">
          <p>伺服器</p>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': settingsSection === 'providers' }"
            type="button"
            @click="selectSettingsSection('providers')"
          >
            <span aria-hidden="true">⚙</span>
            提供者
          </button>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': settingsSection === 'models' }"
            type="button"
            @click="selectSettingsSection('models')"
          >
            <span aria-hidden="true">✦</span>
            模型
          </button>
        </div>

        <div class="settings-nav__footer">
          <strong>OpenSprite Web</strong>
          <span>v0.1.0</span>
        </div>
      </aside>

      <div class="settings-content">
        <header class="settings-content__header">
          <h2 id="settingsTitle">{{ settingsTitle }}</h2>
          <button class="settings-panel__close" type="button" aria-label="Close settings" @click="closeSettings">
            Close
          </button>
        </header>

        <section v-show="settingsSection === 'general'" class="settings-page">
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>語言</strong>
                <span>變更 OpenSprite 的顯示語言</span>
              </div>
              <select aria-label="Language">
                <option>繁體中文</option>
                <option>English</option>
              </select>
            </div>

            <label class="settings-row">
              <div>
                <strong>自動接受權限</strong>
                <span>權限請求將被自動批准</span>
              </div>
              <input class="switch" type="checkbox" />
            </label>

            <label class="settings-row">
              <div>
                <strong>顯示推理摘要</strong>
                <span>在時間軸中顯示模型推理摘要</span>
              </div>
              <input class="switch" type="checkbox" />
            </label>

            <label class="settings-row">
              <div>
                <strong>展開 shell 工具區塊</strong>
                <span>在時間軸中預設展開 shell 工具區塊</span>
              </div>
              <input class="switch" type="checkbox" />
            </label>

            <label class="settings-row">
              <div>
                <strong>展開 edit 工具區塊</strong>
                <span>在時間軸中預設展開 edit、write 和 patch 工具區塊</span>
              </div>
              <input class="switch" type="checkbox" />
            </label>

            <label class="settings-row">
              <div>
                <strong>顯示工作階段進度列</strong>
                <span>當代理程式正在運作時，在工作階段頂部顯示動畫進度列</span>
              </div>
              <input class="switch" type="checkbox" checked />
            </label>
          </div>

          <h3>連線</h3>
          <div class="settings-card settings-card--form">
            <label class="settings-row settings-row--field">
              <div>
                <strong>WebSocket URL</strong>
                <span>OpenSprite gateway 的連線位置</span>
              </div>
              <input v-model="settingsForm.wsUrl" type="text" spellcheck="false" />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>Display name</strong>
                <span>送出訊息時顯示的使用者名稱</span>
              </div>
              <input v-model="settingsForm.displayName" type="text" maxlength="60" />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>Chat ID</strong>
                <span>用固定 ID 將瀏覽器分頁綁定到同一個 session</span>
              </div>
              <input v-model="settingsForm.chatId" type="text" spellcheck="false" />
            </label>

            <div class="settings-panel__actions">
              <button class="primary-button" type="button" @click="saveSettingsAndConnect">Save and connect</button>
              <button class="secondary-button" type="button" @click="disconnectFromSettings">Disconnect</button>
            </div>
          </div>

          <h3>外觀</h3>
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>配色方案</strong>
                <span>選擇 OpenSprite 要跟隨系統、淺色或深色主題</span>
              </div>
              <select aria-label="Color scheme">
                <option>系統</option>
                <option>淺色</option>
                <option>深色</option>
              </select>
            </div>
          </div>
        </section>

        <section v-show="settingsSection === 'shortcuts'" class="settings-page">
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>開啟設定</strong>
                <span>快速開啟這個設定視窗</span>
              </div>
              <div class="shortcut-keys"><kbd>Ctrl</kbd><kbd>,</kbd></div>
            </div>
            <div class="settings-row">
              <div>
                <strong>送出訊息</strong>
                <span>在輸入框中送出目前訊息</span>
              </div>
              <div class="shortcut-keys"><kbd>Enter</kbd></div>
            </div>
          </div>
        </section>

        <section v-show="settingsSection === 'providers'" class="settings-page">
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>提供者設定</strong>
                <span>提供者設定之後會放在這裡</span>
              </div>
              <span class="settings-muted">Coming soon</span>
            </div>
          </div>
        </section>

        <section v-show="settingsSection === 'models'" class="settings-page">
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>模型設定</strong>
                <span>模型清單與預設模型之後會放在這裡</span>
              </div>
              <span class="settings-muted">Coming soon</span>
            </div>
          </div>
        </section>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

const STORAGE_KEYS = {
  wsUrl: "opensprite:web:wsUrl",
  displayName: "opensprite:web:displayName",
  activeChatId: "opensprite:web:activeChatId",
};

const SETTINGS_TITLES = {
  general: "一般",
  shortcuts: "快速鍵",
  providers: "提供者",
  models: "模型",
};

const MAX_RUN_EVENTS = 80;
const MAX_TIMELINE_EVENTS = 8;
const TIMELINE_EVENT_TYPES = new Set([
  "run_started",
  "llm_status",
  "tool_started",
  "verification_started",
  "verification_result",
  "run_finished",
  "run_failed",
]);

const prompts = [
  {
    title: "Summarize capabilities",
    description: "Get a quick overview of the local assistant.",
    text: "Summarize what OpenSprite can do in five bullets.",
  },
  {
    title: "Plan a feature",
    description: "Turn an idea into a practical implementation path.",
    text: "Help me plan the next feature for this project.",
  },
  {
    title: "Review structure",
    description: "Ask for architecture and maintainability feedback.",
    text: "Review the current project structure and suggest improvements.",
  },
  {
    title: "Draft docs",
    description: "Create clear project documentation quickly.",
    text: "Draft a concise README section for the web gateway.",
  },
];

function resolveDefaultWsUrl() {
  if (window.location.protocol === "http:" || window.location.protocol === "https:") {
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProtocol}//${window.location.host}/ws`;
  }
  return "ws://127.0.0.1:8765/ws";
}

const DEFAULT_WS_URL = resolveDefaultWsUrl();

function readStoredValue(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function writeStoredValue(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    return;
  }
}

function randomToken() {
  return Math.random().toString(36).slice(2, 8);
}

function generateChatId() {
  return `browser-${Date.now().toString(36)}-${randomToken()}`;
}

function summarizeTitle(text) {
  const singleLine = text.trim().replace(/\s+/g, " ");
  if (!singleLine) {
    return "New chat";
  }
  return singleLine.length > 30 ? `${singleLine.slice(0, 30)}...` : singleLine;
}

function makeMessage(role, text, meta) {
  return {
    id: `msg-${Date.now().toString(36)}-${randomToken()}`,
    role,
    text,
    meta,
    createdAt: Date.now(),
  };
}

function createSession(chatId) {
  return {
    chatId: chatId || generateChatId(),
    sessionChatId: null,
    title: "New chat",
    updatedAt: Date.now(),
    messages: [],
    activeRunId: null,
    runs: [],
  };
}

const storedChatId = readStoredValue(STORAGE_KEYS.activeChatId, "");
const initialSession = createSession(storedChatId || generateChatId());

const state = reactive({
  wsUrl: readStoredValue(STORAGE_KEYS.wsUrl, DEFAULT_WS_URL),
  displayName: readStoredValue(STORAGE_KEYS.displayName, "Local browser"),
  activeChatId: initialSession.chatId,
  sessions: [initialSession],
  connectionState: "disconnected",
  notice: {
    text: "Connecting to your local OpenSprite gateway...",
    tone: "info",
  },
});

const messageText = ref("");
const messageInput = ref(null);
const messageStage = ref(null);
const sidebarOpen = ref(false);
const settingsOpen = ref(false);
const settingsSection = ref("general");
const settingsForm = reactive({
  wsUrl: state.wsUrl,
  displayName: state.displayName,
  chatId: state.activeChatId,
});

let activeSocket = null;

const currentSession = computed(() => {
  return state.sessions.find((session) => session.chatId === state.activeChatId) || null;
});

const currentMessages = computed(() => currentSession.value?.messages || []);

const currentRun = computed(() => {
  const session = currentSession.value;
  if (!session?.runs?.length) {
    return null;
  }
  return session.runs.find((run) => run.runId === session.activeRunId) || session.runs[0];
});

const currentRunTimeline = computed(() => {
  const events = currentRun.value?.events || [];
  return events.slice(-MAX_TIMELINE_EVENTS);
});

const currentRunSummary = computed(() => {
  const run = currentRun.value;
  const latestEvent = currentRunTimeline.value.at(-1);
  if (!run || !latestEvent) {
    return null;
  }
  return {
    shortId: shortRunId(run.runId),
    statusLabel: runStatusLabel(run.status),
    title: latestEvent.label,
    tone: runTone(run.status, latestEvent.tone),
  };
});

const settingsTitle = computed(() => SETTINGS_TITLES[settingsSection.value] || SETTINGS_TITLES.general);

const sessionMeta = computed(() => {
  const session = currentSession.value;
  return `${session?.title || "New chat"} · ${getSessionDisplayId(session)}`;
});

const runtimeHint = computed(() => currentSession.value?.chatId || "No active chat");

const connectionLabel = computed(() => {
  const labels = {
    disconnected: "Disconnected",
    connecting: "Connecting",
    connected: "Connected",
  };
  return labels[state.connectionState] || labels.disconnected;
});

const connectButtonLabel = computed(() => {
  const labels = {
    disconnected: "Retry",
    connecting: "Connecting",
    connected: "Reconnect",
  };
  return labels[state.connectionState] || labels.disconnected;
});

const statusDotClass = computed(() => ({
  "status-dot--connected": state.connectionState === "connected",
  "status-dot--connecting": state.connectionState === "connecting",
}));

const sendDisabled = computed(() => state.connectionState !== "connected");

watch(settingsOpen, (isOpen) => {
  document.body.classList.toggle("settings-open", isOpen);
});

watch(sidebarOpen, (isOpen) => {
  document.body.classList.toggle("sidebar-open", isOpen);
});

function sortSessions() {
  state.sessions.sort((left, right) => right.updatedAt - left.updatedAt);
}

function getSessionDisplayId(session) {
  if (!session) {
    return "No active chat";
  }
  return session.sessionChatId || `web:${session.chatId}`;
}

function ensureSession(chatId, sessionChatId) {
  const resolvedChatId = chatId || generateChatId();
  let session = state.sessions.find((entry) => entry.chatId === resolvedChatId);
  if (!session) {
    session = createSession(resolvedChatId);
    session.messages = [
      makeMessage(
        "assistant",
        "This thread was created from the live gateway. Send a message to continue the conversation.",
        "OpenSprite",
      ),
    ];
    state.sessions.unshift(session);
  }
  if (sessionChatId) {
    session.sessionChatId = sessionChatId;
  }
  session.updatedAt = Date.now();
  return session;
}

function addMessage(chatId, message) {
  const session = ensureSession(chatId);
  session.messages.push(message);
  session.updatedAt = message.createdAt;
  if (message.role === "user" && session.title === "New chat") {
    session.title = summarizeTitle(message.text);
  }
  sortSessions();
}

function normalizeEventTimestamp(value) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue) || numericValue <= 0) {
    return Date.now();
  }
  return numericValue > 1_000_000_000_000 ? numericValue : numericValue * 1000;
}

function coerceEventPayload(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function shortRunId(runId) {
  const normalized = String(runId || "run").replace(/^run[_-]?/, "");
  return normalized.length > 8 ? normalized.slice(0, 8) : normalized;
}

function runStatusLabel(status) {
  const labels = {
    running: "Running",
    completed: "Complete",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  return labels[status] || "Running";
}

function runTone(status, fallbackTone = "running") {
  if (status === "completed") {
    return fallbackTone === "warning" ? "warning" : "success";
  }
  if (status === "failed") {
    return "error";
  }
  if (status === "cancelled") {
    return "warning";
  }
  return fallbackTone || "running";
}

function statusFromRunEvent(eventType, payload) {
  if (eventType === "run_started") {
    return "running";
  }
  if (eventType === "run_finished") {
    return payload.status || "completed";
  }
  if (eventType === "run_failed") {
    return payload.status || "failed";
  }
  return null;
}

function formatRunFinishDetail(payload) {
  const parts = [];
  if (Number.isFinite(Number(payload.executed_tool_calls))) {
    parts.push(`${payload.executed_tool_calls} tool call(s)`);
  }
  if (Number.isFinite(Number(payload.context_compactions)) && Number(payload.context_compactions) > 0) {
    parts.push(`${payload.context_compactions} compaction(s)`);
  }
  if (payload.had_tool_error) {
    parts.push("tool warning recorded");
  }
  return parts.join(" · ");
}

function describeRunEvent(eventType, payload) {
  if (!TIMELINE_EVENT_TYPES.has(eventType)) {
    return null;
  }

  if (eventType === "run_started") {
    return { label: "Run started", detail: "Preparing the local task.", tone: "running" };
  }

  if (eventType === "llm_status") {
    const message = String(payload.message || "Thinking");
    return {
      label: message === "processing" ? "Thinking" : "LLM status",
      detail: message === "processing" ? "Preparing prompt and tool context." : message,
      tone: "running",
    };
  }

  if (eventType === "tool_started") {
    if (payload.tool_name === "verify") {
      return null;
    }
    return {
      label: `Tool: ${payload.tool_name || "unknown"}`,
      detail: payload.args_preview || "Executing tool.",
      tone: "running",
    };
  }

  if (eventType === "verification_started") {
    return {
      label: `Verifying: ${payload.action || "auto"}`,
      detail: payload.path ? `Path: ${payload.path}` : "Running project checks.",
      tone: "running",
    };
  }

  if (eventType === "verification_result") {
    const ok = payload.ok !== false;
    return {
      label: ok ? "Verification passed" : "Verification failed",
      detail: payload.result_preview || "Verification completed.",
      tone: ok ? "success" : "error",
    };
  }

  if (eventType === "run_finished") {
    return {
      label: payload.had_tool_error ? "Run completed with warnings" : "Run completed",
      detail: formatRunFinishDetail(payload) || "Final response delivered.",
      tone: payload.had_tool_error ? "warning" : "success",
    };
  }

  if (eventType === "run_failed") {
    const cancelled = payload.status === "cancelled";
    return {
      label: cancelled ? "Run cancelled" : "Run failed",
      detail: payload.error || "The run stopped before completion.",
      tone: cancelled ? "warning" : "error",
    };
  }

  return null;
}

function findOrCreateRun(session, runId, createdAt) {
  let run = session.runs.find((entry) => entry.runId === runId);
  if (!run) {
    run = {
      runId,
      status: "running",
      createdAt,
      updatedAt: createdAt,
      events: [],
    };
    session.runs.unshift(run);
  }
  session.activeRunId = runId;
  return run;
}

function handleRunEvent(payload) {
  const chatId = payload.chat_id || currentSession.value?.chatId || generateChatId();
  const session = ensureSession(chatId, payload.session_chat_id);
  const runId = String(payload.run_id || `run-${Date.now().toString(36)}-${randomToken()}`);
  const eventType = String(payload.event_type || "run_event");
  const eventPayload = coerceEventPayload(payload.payload);
  const createdAt = normalizeEventTimestamp(payload.created_at);
  const run = findOrCreateRun(session, runId, createdAt);
  const nextStatus = statusFromRunEvent(eventType, eventPayload);
  if (nextStatus) {
    run.status = nextStatus;
  } else if (!["completed", "failed", "cancelled"].includes(run.status)) {
    run.status = "running";
  }

  const description = describeRunEvent(eventType, eventPayload);
  if (description) {
    run.events.push({
      id: `${runId}-${eventType}-${createdAt}-${randomToken()}`,
      eventType,
      createdAt,
      payload: eventPayload,
      ...description,
    });
    if (run.events.length > MAX_RUN_EVENTS) {
      run.events.splice(0, run.events.length - MAX_RUN_EVENTS);
    }
  }

  run.updatedAt = createdAt;
  session.updatedAt = createdAt;
  session.runs.sort((left, right) => right.updatedAt - left.updatedAt);
  sortSessions();
}

function formatEventTime(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return "--:--";
  }
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

function setNotice(text, tone) {
  state.notice.text = text;
  state.notice.tone = tone;
}

function setActiveSession(chatId) {
  state.activeChatId = chatId;
  writeStoredValue(STORAGE_KEYS.activeChatId, chatId);
  closeSidebar();
}

function selectSettingsSection(sectionName) {
  settingsSection.value = SETTINGS_TITLES[sectionName] ? sectionName : "general";
}

function syncSettingsForm() {
  settingsForm.wsUrl = state.wsUrl;
  settingsForm.displayName = state.displayName;
  settingsForm.chatId = currentSession.value?.chatId || "";
}

function openSettings(sectionName = "general") {
  settingsOpen.value = true;
  selectSettingsSection(sectionName);
  syncSettingsForm();
}

function closeSettings() {
  settingsOpen.value = false;
}

function openSidebar() {
  sidebarOpen.value = true;
}

function closeSidebar() {
  sidebarOpen.value = false;
}

function toggleSidebar() {
  if (sidebarOpen.value) {
    closeSidebar();
    return;
  }
  openSidebar();
}

function disconnectSocket(reason, tone = "warning") {
  const socket = activeSocket;
  activeSocket = null;
  state.connectionState = "disconnected";
  if (socket && socket.readyState !== WebSocket.CLOSED) {
    socket.close(1000, "Client disconnect");
  }
  setNotice(reason, tone);
}

function buildSocketUrl(baseUrl, chatId) {
  const url = new URL(baseUrl);
  url.searchParams.set("chat_id", chatId);
  return url.toString();
}

function handleSocketMessage(rawData) {
  let payload;
  try {
    payload = JSON.parse(rawData);
  } catch {
    setNotice("The gateway sent a payload that could not be parsed.", "error");
    return;
  }

  if (payload.type === "session") {
    const session = ensureSession(payload.chat_id, payload.session_chat_id);
    if (!state.activeChatId) {
      state.activeChatId = session.chatId;
    }
    setNotice(`Live session ready: ${payload.session_chat_id}`, "success");
    return;
  }

  if (payload.type === "message") {
    const chatId = payload.chat_id || currentSession.value?.chatId || generateChatId();
    const session = ensureSession(chatId, payload.session_chat_id);
    addMessage(session.chatId, makeMessage("assistant", payload.text || "", payload.session_chat_id || "OpenSprite"));
    scrollMessagesToBottom();
    return;
  }

  if (payload.type === "run_event") {
    handleRunEvent(payload);
    scrollMessagesToBottom();
    return;
  }

  if (payload.type === "error") {
    setNotice(payload.error || "The gateway returned an error.", "error");
  }
}

function connectSocket() {
  const session = currentSession.value;
  if (!session) {
    return;
  }

  let socketUrl;
  try {
    socketUrl = buildSocketUrl(state.wsUrl, session.chatId);
  } catch {
    setNotice("The WebSocket URL is invalid. Check it in settings first.", "error");
    openSettings("general");
    return;
  }

  if (activeSocket) {
    disconnectSocket("Refreshing the connection...", "info");
  }

  state.connectionState = "connecting";
  setNotice(`Connecting to ${state.wsUrl}`, "info");

  const socket = new WebSocket(socketUrl);
  activeSocket = socket;

  socket.addEventListener("open", () => {
    if (activeSocket !== socket) {
      return;
    }
    state.connectionState = "connected";
    setNotice("Connected. Send a message to talk to your local gateway.", "success");
  });

  socket.addEventListener("message", (event) => {
    if (activeSocket !== socket) {
      return;
    }
    handleSocketMessage(event.data);
  });

  socket.addEventListener("error", () => {
    if (activeSocket !== socket) {
      return;
    }
    setNotice("The WebSocket connection failed. Make sure `opensprite gateway` is running.", "error");
  });

  socket.addEventListener("close", () => {
    if (activeSocket !== socket) {
      return;
    }
    const failedToConnect = state.connectionState === "connecting";
    activeSocket = null;
    state.connectionState = "disconnected";
    setNotice(
      failedToConnect ? "Could not connect. Start the gateway, then try again." : "Disconnected from the gateway.",
      failedToConnect ? "error" : "warning",
    );
  });
}

function resizeComposer() {
  const input = messageInput.value;
  if (!input) {
    return;
  }
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
}

function scrollMessagesToBottom() {
  nextTick(() => {
    const stage = messageStage.value;
    if (stage) {
      stage.scrollTop = stage.scrollHeight;
    }
  });
}

function createNewChat() {
  const session = createSession();
  state.sessions.unshift(session);
  state.activeChatId = session.chatId;
  writeStoredValue(STORAGE_KEYS.activeChatId, session.chatId);
  setNotice("Started a fresh local draft. Your next live message will use a new chat ID.", "info");
  scrollMessagesToBottom();
}

function saveSettingsAndConnect() {
  state.wsUrl = settingsForm.wsUrl.trim() || DEFAULT_WS_URL;
  state.displayName = settingsForm.displayName.trim() || "Local browser";

  const requestedChatId = settingsForm.chatId.trim();
  if (requestedChatId) {
    ensureSession(requestedChatId);
    state.activeChatId = requestedChatId;
  }

  writeStoredValue(STORAGE_KEYS.wsUrl, state.wsUrl);
  writeStoredValue(STORAGE_KEYS.displayName, state.displayName);
  writeStoredValue(STORAGE_KEYS.activeChatId, state.activeChatId);

  closeSettings();
  connectSocket();
}

function disconnectFromSettings() {
  closeSettings();
  disconnectSocket("Disconnected from the gateway.", "warning");
}

function submitMessage(event) {
  event.preventDefault();
  const text = messageText.value.trim();
  if (!text) {
    return;
  }

  if (!activeSocket || activeSocket.readyState !== WebSocket.OPEN) {
    if (state.connectionState === "connecting") {
      setNotice("Still connecting to the local gateway. Your message can be sent once the status turns connected.", "info");
      return;
    }
    setNotice("The automatic connection is not active. Check the endpoint, then retry.", "warning");
    openSettings("general");
    return;
  }

  const session = currentSession.value;
  if (!session) {
    return;
  }

  addMessage(session.chatId, makeMessage("user", text, state.displayName || "Local browser"));
  activeSocket.send(
    JSON.stringify({
      chat_id: session.chatId,
      sender_name: state.displayName,
      text,
    }),
  );

  messageText.value = "";
  resizeComposer();
  scrollMessagesToBottom();
}

function handleComposerKeydown(event) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    submitMessage(event);
  }
}

function applyPrompt(text) {
  messageText.value = text;
  nextTick(() => {
    resizeComposer();
    messageInput.value?.focus();
  });
}

function handleGlobalKeydown(event) {
  if (event.key === "Escape") {
    closeSettings();
    closeSidebar();
  }
}

onMounted(() => {
  document.addEventListener("keydown", handleGlobalKeydown);
  resizeComposer();
  scrollMessagesToBottom();
  connectSocket();
});

onBeforeUnmount(() => {
  document.removeEventListener("keydown", handleGlobalKeydown);
  document.body.classList.remove("settings-open", "sidebar-open");
  if (activeSocket && activeSocket.readyState !== WebSocket.CLOSED) {
    activeSocket.close(1000, "Client disconnect");
  }
  activeSocket = null;
});
</script>
