import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

const STORAGE_KEYS = {
  wsUrl: "opensprite:web:wsUrl",
  displayName: "opensprite:web:displayName",
  activeChatId: "opensprite:web:activeChatId",
  showRunTimeline: "opensprite:web:showRunTimeline",
  showRunTrace: "opensprite:web:showRunTrace",
};

const SETTINGS_TITLES = {
  general: "一般",
  shortcuts: "快速鍵",
  channels: "頻道",
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

function readStoredBoolean(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    if (value === null) {
      return fallback;
    }
    return value === "true";
  } catch {
    return fallback;
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

function buildHttpApiUrl(wsUrl, pathname) {
  const url = new URL(wsUrl);
  url.protocol = url.protocol === "wss:" ? "https:" : "http:";
  url.pathname = pathname;
  url.search = "";
  return url;
}

function buildRunCancelUrl(wsUrl, runId, chatId) {
  const url = buildHttpApiUrl(wsUrl, `/api/runs/${encodeURIComponent(runId)}/cancel`);
  url.searchParams.set("chat_id", chatId);
  return url.toString();
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

export function formatEventTime(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) {
    return "--:--";
  }
  const hours = String(date.getHours()).padStart(2, "0");
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const seconds = String(date.getSeconds()).padStart(2, "0");
  return `${hours}:${minutes}:${seconds}`;
}

export function useChatClient() {
  const storedChatId = readStoredValue(STORAGE_KEYS.activeChatId, "");
  const initialSession = createSession(storedChatId || generateChatId());

  const state = reactive({
    wsUrl: readStoredValue(STORAGE_KEYS.wsUrl, DEFAULT_WS_URL),
    displayName: readStoredValue(STORAGE_KEYS.displayName, "Local browser"),
    showRunTimeline: readStoredBoolean(STORAGE_KEYS.showRunTimeline, true),
    showRunTrace: readStoredBoolean(STORAGE_KEYS.showRunTrace, true),
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
    showRunTimeline: state.showRunTimeline,
    showRunTrace: state.showRunTrace,
  });
  const settingsState = reactive({
    channelsLoading: false,
    channelsError: "",
    channelsNotice: "",
    channels: {
      connected: [],
      available: [],
      channels: [],
    },
    channelConnectForm: {
      channelId: "",
      token: "",
    },
    providersLoading: false,
    providersError: "",
    providersNotice: "",
    providers: {
      default_provider: null,
      connected: [],
      available: [],
    },
    connectForm: {
      providerId: "",
      apiKey: "",
      baseUrl: "",
      showAdvanced: false,
    },
    modelsLoading: false,
    modelsError: "",
    modelsNotice: "",
    models: {
      default_provider: null,
      active_model: "",
      providers: [],
    },
    customModels: {},
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

  function setMessageInputRef(element) {
    messageInput.value = element;
  }

  function setMessageStageRef(element) {
    messageStage.value = element;
  }

  function setMessageText(value) {
    messageText.value = value;
  }

  function saveRunPanelVisibilitySettings(showRunTimeline, showRunTrace) {
    state.showRunTimeline = Boolean(showRunTimeline);
    state.showRunTrace = Boolean(showRunTrace);
    writeStoredValue(STORAGE_KEYS.showRunTimeline, String(state.showRunTimeline));
    writeStoredValue(STORAGE_KEYS.showRunTrace, String(state.showRunTrace));
  }

  watch(settingsOpen, (isOpen) => {
    document.body.classList.toggle("settings-open", isOpen);
  });

  watch(sidebarOpen, (isOpen) => {
    document.body.classList.toggle("sidebar-open", isOpen);
  });

  watch(
    () => [settingsForm.showRunTimeline, settingsForm.showRunTrace],
    ([showRunTimeline, showRunTrace]) => {
      saveRunPanelVisibilitySettings(showRunTimeline, showRunTrace);
    },
  );

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

  function findOrCreateRun(session, runId, createdAt) {
    let run = session.runs.find((entry) => entry.runId === runId);
    if (!run) {
      run = {
        runId,
        status: "running",
        createdAt,
        updatedAt: createdAt,
        events: [],
        rawEvents: [],
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
    run.rawEvents.push({
      id: `${runId}-raw-${eventType}-${createdAt}-${randomToken()}`,
      eventType,
      createdAt,
      payload: eventPayload,
    });
    if (run.rawEvents.length > MAX_RUN_EVENTS) {
      run.rawEvents.splice(0, run.rawEvents.length - MAX_RUN_EVENTS);
    }

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
    loadSettingsSection(settingsSection.value);
  }

  function syncSettingsForm() {
    settingsForm.wsUrl = state.wsUrl;
    settingsForm.displayName = state.displayName;
    settingsForm.chatId = currentSession.value?.chatId || "";
    settingsForm.showRunTimeline = state.showRunTimeline;
    settingsForm.showRunTrace = state.showRunTrace;
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

  async function requestSettingsJson(pathname, options = {}) {
    const response = await fetch(buildHttpApiUrl(state.wsUrl, pathname).toString(), {
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `HTTP ${response.status}`);
    }
    return response.json();
  }

  async function loadProviderSettings() {
    settingsState.providersLoading = true;
    settingsState.providersError = "";
    try {
      settingsState.providers = await requestSettingsJson("/api/settings/providers");
    } catch (error) {
      settingsState.providersError = error?.message || "Could not load provider settings.";
    } finally {
      settingsState.providersLoading = false;
    }
  }

  function visibleChannels(channels) {
    return (channels || []).filter((channel) => channel.id !== "web" && channel.id !== "console");
  }

  async function loadChannelSettings() {
    settingsState.channelsLoading = true;
    settingsState.channelsError = "";
    try {
      const payload = await requestSettingsJson("/api/settings/channels");
      settingsState.channels = {
        ...payload,
        connected: visibleChannels(payload.connected),
        available: visibleChannels(payload.available),
        channels: visibleChannels(payload.channels),
      };
    } catch (error) {
      settingsState.channelsError = error?.message || "無法載入頻道設定。";
    } finally {
      settingsState.channelsLoading = false;
    }
  }

  async function loadModelSettings() {
    settingsState.modelsLoading = true;
    settingsState.modelsError = "";
    try {
      settingsState.models = await requestSettingsJson("/api/settings/models");
      for (const provider of settingsState.models.providers || []) {
        if (!Object.prototype.hasOwnProperty.call(settingsState.customModels, provider.id)) {
          settingsState.customModels[provider.id] = "";
        }
      }
    } catch (error) {
      settingsState.modelsError = error?.message || "Could not load model settings.";
    } finally {
      settingsState.modelsLoading = false;
    }
  }

  function loadSettingsSection(sectionName) {
    if (sectionName === "channels") {
      loadChannelSettings();
      return;
    }
    if (sectionName === "providers") {
      loadProviderSettings();
      return;
    }
    if (sectionName === "models") {
      loadModelSettings();
    }
  }

  function beginChannelConnect(channel) {
    settingsState.channelsNotice = "";
    settingsState.channelsError = "";
    cancelProviderConnect();
    settingsState.channelConnectForm.channelId = channel.id;
    settingsState.channelConnectForm.token = "";
  }

  function cancelChannelConnect() {
    settingsState.channelConnectForm.channelId = "";
    settingsState.channelConnectForm.token = "";
  }

  async function saveChannelConnection() {
    const channelId = settingsState.channelConnectForm.channelId;
    if (!channelId) {
      return;
    }
    settingsState.channelsLoading = true;
    settingsState.channelsError = "";
    settingsState.channelsNotice = "";
    try {
      const payload = await requestSettingsJson(`/api/settings/channels/${encodeURIComponent(channelId)}/connect`, {
        method: "PUT",
        body: JSON.stringify({
          token: settingsState.channelConnectForm.token,
        }),
      });
      settingsState.channelsNotice = payload.restart_required
        ? `${payload.channel.name} 已連線，重啟 opensprite gateway 後生效。`
        : `${payload.channel.name} 已連線。`;
      cancelChannelConnect();
      await loadChannelSettings();
    } catch (error) {
      settingsState.channelsError = error?.message || "無法連線頻道。";
    } finally {
      settingsState.channelsLoading = false;
    }
  }

  async function disconnectChannel(channel) {
    settingsState.channelsLoading = true;
    settingsState.channelsError = "";
    settingsState.channelsNotice = "";
    try {
      const payload = await requestSettingsJson(`/api/settings/channels/${encodeURIComponent(channel.id)}/disconnect`, {
        method: "POST",
      });
      settingsState.channelsNotice = payload.restart_required
        ? `${channel.name} 已中斷連線，重啟 opensprite gateway 後生效。`
        : `${channel.name} 已中斷連線。`;
      await loadChannelSettings();
    } catch (error) {
      settingsState.channelsError = error?.message || "無法中斷頻道連線。";
    } finally {
      settingsState.channelsLoading = false;
    }
  }

  function beginProviderConnect(provider) {
    settingsState.providersNotice = "";
    settingsState.providersError = "";
    cancelChannelConnect();
    settingsState.connectForm.providerId = provider.id;
    settingsState.connectForm.apiKey = "";
    settingsState.connectForm.baseUrl = provider.default_base_url || provider.base_url || "";
    settingsState.connectForm.showAdvanced = false;
  }

  function cancelProviderConnect() {
    settingsState.connectForm.providerId = "";
    settingsState.connectForm.apiKey = "";
    settingsState.connectForm.baseUrl = "";
    settingsState.connectForm.showAdvanced = false;
  }

  async function saveProviderConnection() {
    const providerId = settingsState.connectForm.providerId;
    if (!providerId) {
      return;
    }
    settingsState.providersLoading = true;
    settingsState.providersError = "";
    settingsState.providersNotice = "";
    try {
      await requestSettingsJson(`/api/settings/providers/${encodeURIComponent(providerId)}/connect`, {
        method: "PUT",
        body: JSON.stringify({
          api_key: settingsState.connectForm.apiKey,
          base_url: settingsState.connectForm.baseUrl,
        }),
      });
      settingsState.providersNotice = "已連線，請到模型頁選擇使用的模型。";
      cancelProviderConnect();
      await loadProviderSettings();
      await loadModelSettings();
    } catch (error) {
      settingsState.providersError = error?.message || "Could not connect provider.";
    } finally {
      settingsState.providersLoading = false;
    }
  }

  async function disconnectProvider(provider) {
    settingsState.providersLoading = true;
    settingsState.providersError = "";
    settingsState.providersNotice = "";
    try {
      const payload = await requestSettingsJson(`/api/settings/providers/${encodeURIComponent(provider.id)}/disconnect`, {
        method: "POST",
      });
      settingsState.providersNotice = payload.restart_required
        ? `${provider.name} 已中斷連線，重啟 opensprite gateway 後生效。`
        : `${provider.name} 已中斷連線。`;
      await loadProviderSettings();
      await loadModelSettings();
    } catch (error) {
      settingsState.providersError = error?.message || "Could not disconnect provider.";
    } finally {
      settingsState.providersLoading = false;
    }
  }

  async function selectModel(providerId, model) {
    const normalizedModel = String(model || "").trim();
    if (!normalizedModel) {
      settingsState.modelsError = "請先輸入模型名稱。";
      return;
    }

    settingsState.modelsLoading = true;
    settingsState.modelsError = "";
    settingsState.modelsNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/models/select", {
        method: "POST",
        body: JSON.stringify({ provider_id: providerId, model: normalizedModel }),
      });
      settingsState.modelsNotice = payload.restart_required
        ? "已儲存，重啟 opensprite gateway 後生效。"
        : "已套用模型設定。";
      settingsState.customModels[providerId] = "";
      await loadModelSettings();
      await loadProviderSettings();
    } catch (error) {
      settingsState.modelsError = error?.message || "Could not select model.";
    } finally {
      settingsState.modelsLoading = false;
    }
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

  function saveConnectionSettings() {
    state.wsUrl = settingsForm.wsUrl.trim() || DEFAULT_WS_URL;
    state.displayName = settingsForm.displayName.trim() || "Local browser";
    saveRunPanelVisibilitySettings(settingsForm.showRunTimeline, settingsForm.showRunTrace);

    const requestedChatId = settingsForm.chatId.trim();
    if (requestedChatId) {
      ensureSession(requestedChatId);
      state.activeChatId = requestedChatId;
    }

    writeStoredValue(STORAGE_KEYS.wsUrl, state.wsUrl);
    writeStoredValue(STORAGE_KEYS.displayName, state.displayName);
    writeStoredValue(STORAGE_KEYS.activeChatId, state.activeChatId);
  }

  function toggleSettingsConnection(shouldConnect) {
    if (shouldConnect) {
      saveConnectionSettings();
      connectSocket();
      return;
    }
    disconnectSocket("Disconnected from the gateway.", "warning");
  }

  async function cancelRun(run) {
    const session = currentSession.value;
    if (!session || !run?.runId || run.status !== "running") {
      return;
    }

    run.cancelPending = true;
    try {
      const response = await fetch(buildRunCancelUrl(state.wsUrl, run.runId, session.chatId), { method: "POST" });
      if (!response.ok) {
        throw new Error(`Cancel request failed with HTTP ${response.status}`);
      }
      setNotice(`Cancel requested for run ${run.runId}.`, "warning");
    } catch (error) {
      setNotice(error?.message || "Could not request run cancellation.", "error");
    } finally {
      run.cancelPending = false;
    }
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

  return {
    prompts,
    state,
    messageText,
    messageInput,
    messageStage,
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
    loadProviderSettings,
    loadModelSettings,
    loadChannelSettings,
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
  };
}
