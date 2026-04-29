import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { getDisplayCopy } from "../i18n/copy";

const STORAGE_KEYS = {
  wsUrl: "opensprite:web:wsUrl",
  displayName: "opensprite:web:displayName",
  activeExternalChatId: "opensprite:web:activeExternalChatId",
  showRunTimeline: "opensprite:web:showRunTimeline",
  showRunTrace: "opensprite:web:showRunTrace",
  language: "opensprite:web:language",
  colorScheme: "opensprite:web:colorScheme",
  sidebarCollapsed: "opensprite:web:sidebarCollapsed",
};

const DEFAULT_LANGUAGE = "zh-TW";
const DEFAULT_COLOR_SCHEME = "system";
const SUPPORTED_LANGUAGES = new Set(["zh-TW", "en"]);
const SUPPORTED_COLOR_SCHEMES = new Set(["system", "light", "dark"]);
const LANGUAGE_ATTRIBUTES = {
  "zh-TW": "zh-Hant-TW",
  en: "en",
};

const MAX_RUN_EVENTS = 80;
const MAX_TIMELINE_EVENTS = 8;
const MCP_TRANSPORT_TYPES = new Set(["stdio", "sse", "streamableHttp"]);
const TIMELINE_EVENT_TYPES = new Set([
  "run_started",
  "llm_status",
  "tool_started",
  "verification_started",
  "verification_result",
  "run_finished",
  "run_failed",
]);

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

function normalizeChoice(value, fallback, allowedValues) {
  const normalized = String(value || "").trim();
  return allowedValues.has(normalized) ? normalized : fallback;
}

function readStoredChoice(key, fallback, allowedValues) {
  return normalizeChoice(readStoredValue(key, fallback), fallback, allowedValues);
}

function getResolvedColorScheme(colorScheme) {
  if (colorScheme !== "system") {
    return colorScheme;
  }
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
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

function generateExternalChatId() {
  return `browser-${Date.now().toString(36)}-${randomToken()}`;
}

function externalChatIdFromSessionId(sessionId) {
  const normalized = String(sessionId || "").trim();
  const separatorIndex = normalized.indexOf(":");
  if (separatorIndex < 0) {
    return normalized;
  }
  return normalized.slice(separatorIndex + 1).trim();
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

function createSession(externalChatId) {
  return {
    externalChatId: externalChatId || generateExternalChatId(),
    sessionId: null,
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

function runStatusLabel(status, copy) {
  return copy.run.statusLabels[status] || copy.run.statusLabels.running;
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

function buildRunCancelUrl(wsUrl, runId, sessionId) {
  const url = buildHttpApiUrl(wsUrl, `/api/runs/${encodeURIComponent(runId)}/cancel`);
  url.searchParams.set("session_id", sessionId);
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

function formatRunFinishDetail(payload, copy) {
  const parts = [];
  if (Number.isFinite(Number(payload.executed_tool_calls))) {
    parts.push(copy.run.toolCalls(payload.executed_tool_calls));
  }
  if (Number.isFinite(Number(payload.context_compactions)) && Number(payload.context_compactions) > 0) {
    parts.push(copy.run.compactions(payload.context_compactions));
  }
  if (payload.had_tool_error) {
    parts.push(copy.run.toolWarning);
  }
  return parts.join(" · ");
}

function describeRunEvent(eventType, payload, copy) {
  if (!TIMELINE_EVENT_TYPES.has(eventType)) {
    return null;
  }

  if (eventType === "run_started") {
    return { label: copy.run.runStarted, detail: copy.run.preparingTask, tone: "running" };
  }

  if (eventType === "llm_status") {
    const message = String(payload.message || copy.run.thinking);
    return {
      label: message === "processing" ? copy.run.thinking : copy.run.llmStatus,
      detail: message === "processing" ? copy.run.preparingPrompt : message,
      tone: "running",
    };
  }

  if (eventType === "tool_started") {
    if (payload.tool_name === "verify") {
      return null;
    }
    return {
      label: `${copy.run.tool}: ${payload.tool_name || copy.run.unknownTool}`,
      detail: payload.args_preview || copy.run.executingTool,
      tone: "running",
    };
  }

  if (eventType === "verification_started") {
    return {
      label: `${copy.run.verifying}: ${payload.action || copy.run.auto}`,
      detail: payload.path ? `${copy.run.pathPrefix} ${payload.path}` : copy.run.runningChecks,
      tone: "running",
    };
  }

  if (eventType === "verification_result") {
    const ok = payload.ok !== false;
    return {
      label: ok ? copy.run.verificationPassed : copy.run.verificationFailed,
      detail: payload.result_preview || copy.run.verificationCompleted,
      tone: ok ? "success" : "error",
    };
  }

  if (eventType === "run_finished") {
    return {
      label: payload.had_tool_error ? copy.run.completedWithWarnings : copy.run.completed,
      detail: formatRunFinishDetail(payload, copy) || copy.run.finalDelivered,
      tone: payload.had_tool_error ? "warning" : "success",
    };
  }

  if (eventType === "run_failed") {
    const cancelled = payload.status === "cancelled";
    return {
      label: cancelled ? copy.run.cancelled : copy.run.failed,
      detail: payload.error || copy.run.stopped,
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
  const storedExternalChatId = readStoredValue(STORAGE_KEYS.activeExternalChatId, "");
  const initialLanguage = readStoredChoice(STORAGE_KEYS.language, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES);
  const initialColorScheme = readStoredChoice(STORAGE_KEYS.colorScheme, DEFAULT_COLOR_SCHEME, SUPPORTED_COLOR_SCHEMES);
  const initialCopy = getDisplayCopy(initialLanguage);
  const initialSession = createSession(storedExternalChatId || generateExternalChatId());

  const state = reactive({
    wsUrl: readStoredValue(STORAGE_KEYS.wsUrl, DEFAULT_WS_URL),
    displayName: readStoredValue(STORAGE_KEYS.displayName, "Local browser"),
    showRunTimeline: readStoredBoolean(STORAGE_KEYS.showRunTimeline, true),
    showRunTrace: readStoredBoolean(STORAGE_KEYS.showRunTrace, true),
    language: initialLanguage,
    colorScheme: initialColorScheme,
    activeExternalChatId: initialSession.externalChatId,
    sessions: [initialSession],
    connectionState: "disconnected",
    notice: {
      text: initialCopy.notices.connectingGateway,
      tone: "info",
    },
  });

  const copy = computed(() => getDisplayCopy(state.language));
  const prompts = computed(() => copy.value.prompts);

  const messageText = ref("");
  const messageInput = ref(null);
  const messageStage = ref(null);
  const sidebarOpen = ref(false);
  const sidebarCollapsed = ref(readStoredBoolean(STORAGE_KEYS.sidebarCollapsed, false));
  const settingsOpen = ref(false);
  const settingsSection = ref("general");
  const settingsForm = reactive({
    wsUrl: state.wsUrl,
    displayName: state.displayName,
    externalChatId: state.activeExternalChatId,
    showRunTimeline: state.showRunTimeline,
    showRunTrace: state.showRunTrace,
    language: state.language,
    colorScheme: state.colorScheme,
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
      type: "",
      name: "",
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
    scheduleLoading: false,
    scheduleError: "",
    scheduleNotice: "",
    schedule: {
      default_timezone: "UTC",
      common_timezones: [],
    },
    scheduleForm: {
      defaultTimezone: "UTC",
    },
    cronJobsLoading: false,
    cronJobsError: "",
    cronJobsNotice: "",
    cronJobs: [],
    cronJobForm: {
      sessionId: "",
      jobId: "",
      mode: "cron",
      name: "",
      message: "",
      everySeconds: "3600",
      cronExpr: "0 9 * * *",
      at: "",
      timezone: "UTC",
      deliver: true,
    },
    mcpLoading: false,
    mcpError: "",
    mcpNotice: "",
    mcp: {
      servers: [],
      runtime: {
        connected: false,
        connecting: false,
        connect_failures: 0,
        tool_names: [],
      },
    },
    mcpForm: {
      showEditor: false,
      editingId: "",
      serverId: "",
      type: "stdio",
      command: "",
      argsText: "",
      url: "",
      envJson: "",
      headersJson: "",
      toolTimeout: "30",
      enabledToolsText: "*",
      showAdvanced: false,
      showJsonInput: false,
      jsonText: "",
    },
  });

  let activeSocket = null;
  let colorSchemeMediaQuery = null;
  let clientDisposed = false;

  function applyDocumentPreferences() {
    if (typeof document === "undefined") {
      return;
    }
    document.documentElement.lang = LANGUAGE_ATTRIBUTES[state.language] || LANGUAGE_ATTRIBUTES[DEFAULT_LANGUAGE];
    document.documentElement.dataset.colorScheme = getResolvedColorScheme(state.colorScheme);
    document.documentElement.dataset.colorSchemePreference = state.colorScheme;
  }

  function handleSystemColorSchemeChange() {
    if (state.colorScheme === "system") {
      applyDocumentPreferences();
    }
  }

  function addColorSchemeListener() {
    if (typeof window === "undefined" || !window.matchMedia) {
      return;
    }
    colorSchemeMediaQuery = window.matchMedia("(prefers-color-scheme: dark)");
    if (colorSchemeMediaQuery.addEventListener) {
      colorSchemeMediaQuery.addEventListener("change", handleSystemColorSchemeChange);
      return;
    }
    colorSchemeMediaQuery.addListener?.(handleSystemColorSchemeChange);
  }

  function removeColorSchemeListener() {
    if (!colorSchemeMediaQuery) {
      return;
    }
    if (colorSchemeMediaQuery.removeEventListener) {
      colorSchemeMediaQuery.removeEventListener("change", handleSystemColorSchemeChange);
    } else {
      colorSchemeMediaQuery.removeListener?.(handleSystemColorSchemeChange);
    }
    colorSchemeMediaQuery = null;
  }

  const currentSession = computed(() => {
    return state.sessions.find((session) => session.externalChatId === state.activeExternalChatId) || null;
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
      statusLabel: runStatusLabel(run.status, copy.value),
      title: latestEvent.label,
      tone: runTone(run.status, latestEvent.tone),
    };
  });

  const settingsTitle = computed(() => copy.value.settingsTitles[settingsSection.value] || copy.value.settingsTitles.general);

  const sessionMeta = computed(() => {
    const session = currentSession.value;
    return `${getSessionTitle(session)} · ${getSessionDisplayId(session)}`;
  });

  const runtimeHint = computed(() => currentSession.value?.externalChatId || copy.value.session.noActiveChat);

  const connectionLabel = computed(() => {
    const labels = copy.value.connection;
    return labels[state.connectionState] || labels.disconnected;
  });

  const connectButtonLabel = computed(() => {
    const labels = {
      disconnected: copy.value.connection.retry,
      connecting: copy.value.connection.connecting,
      connected: copy.value.connection.reconnect,
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

  function saveDisplaySettings(language, colorScheme) {
    state.language = normalizeChoice(language, DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES);
    state.colorScheme = normalizeChoice(colorScheme, DEFAULT_COLOR_SCHEME, SUPPORTED_COLOR_SCHEMES);
    writeStoredValue(STORAGE_KEYS.language, state.language);
    writeStoredValue(STORAGE_KEYS.colorScheme, state.colorScheme);
    applyDocumentPreferences();
  }

  function rebuildLocalizedRunEvents() {
    for (const session of state.sessions) {
      for (const run of session.runs || []) {
        run.events = (run.rawEvents || [])
          .map((event) => {
            const description = describeRunEvent(event.eventType, event.payload, copy.value);
            return description
              ? {
                  id: `${event.id}-localized`,
                  eventType: event.eventType,
                  createdAt: event.createdAt,
                  payload: event.payload,
                  ...description,
                }
              : null;
          })
          .filter(Boolean)
          .slice(-MAX_RUN_EVENTS);
      }
    }
  }

  watch(settingsOpen, (isOpen) => {
    document.body.classList.toggle("settings-open", isOpen);
  });

  watch(
    () => state.activeExternalChatId,
    () => {
      if (settingsOpen.value && settingsSection.value === "schedule") {
        loadCronJobs();
      }
    },
  );

  watch(sidebarOpen, (isOpen) => {
    document.body.classList.toggle("sidebar-open", isOpen);
  });

  watch(
    () => [settingsForm.showRunTimeline, settingsForm.showRunTrace],
    ([showRunTimeline, showRunTrace]) => {
      saveRunPanelVisibilitySettings(showRunTimeline, showRunTrace);
    },
  );

  watch(
    () => [settingsForm.language, settingsForm.colorScheme],
    ([language, colorScheme]) => {
      saveDisplaySettings(language, colorScheme);
    },
  );

  watch(
    () => [state.language, state.colorScheme],
    ([language], [previousLanguage] = []) => {
      applyDocumentPreferences();
      if (previousLanguage && language !== previousLanguage) {
        rebuildLocalizedRunEvents();
      }
    },
    { immediate: true },
  );

  function sortSessions() {
    state.sessions.sort((left, right) => right.updatedAt - left.updatedAt);
  }

  function getSessionDisplayId(session) {
    if (!session) {
      return copy.value.session.noActiveChat;
    }
    return session.sessionId || session.externalChatId;
  }

  function getSessionApiId(session) {
    return session?.sessionId || "";
  }

  function getSessionTitle(session) {
    if (!session || session.title === "New chat") {
      return copy.value.session.newChat;
    }
    return session.title;
  }

  function ensureSession(externalChatId, sessionId) {
    const resolvedExternalChatId = externalChatId || generateExternalChatId();
    let session = state.sessions.find((entry) => entry.externalChatId === resolvedExternalChatId);
    if (!session) {
      session = createSession(resolvedExternalChatId);
      session.messages = [
        makeMessage(
          "assistant",
          copy.value.session.liveGatewayThread,
          "OpenSprite",
        ),
      ];
      state.sessions.unshift(session);
    }
    if (sessionId) {
      session.sessionId = sessionId;
    }
    session.updatedAt = Date.now();
    return session;
  }

  function addMessage(externalChatId, message) {
    const session = ensureSession(externalChatId);
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
    const externalChatId = payload.external_chat_id || currentSession.value?.externalChatId || generateExternalChatId();
    const session = ensureSession(externalChatId, payload.session_id);
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

    const description = describeRunEvent(eventType, eventPayload, copy.value);
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

  function setActiveSession(externalChatId) {
    state.activeExternalChatId = externalChatId;
    writeStoredValue(STORAGE_KEYS.activeExternalChatId, externalChatId);
    closeSidebar();
  }

  function persistActiveSession() {
    if (state.activeExternalChatId) {
      writeStoredValue(STORAGE_KEYS.activeExternalChatId, state.activeExternalChatId);
    }
  }

  function selectSettingsSection(sectionName) {
    settingsSection.value = Object.prototype.hasOwnProperty.call(copy.value.settingsTitles, sectionName) ? sectionName : "general";
    loadSettingsSection(settingsSection.value);
  }

  function syncSettingsForm() {
    settingsForm.wsUrl = state.wsUrl;
    settingsForm.displayName = state.displayName;
    settingsForm.externalChatId = currentSession.value?.externalChatId || "";
    settingsForm.showRunTimeline = state.showRunTimeline;
    settingsForm.showRunTrace = state.showRunTrace;
    settingsForm.language = state.language;
    settingsForm.colorScheme = state.colorScheme;
  }

  function openSettings(sectionName = "general") {
    settingsOpen.value = true;
    selectSettingsSection(sectionName);
    syncSettingsForm();
  }

  function closeSettings() {
    if (settingsOpen.value) {
      saveConnectionSettings();
    }
    cancelChannelConnect();
    cancelProviderConnect();
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

  function toggleSidebarCollapsed() {
    sidebarCollapsed.value = !sidebarCollapsed.value;
    writeStoredValue(STORAGE_KEYS.sidebarCollapsed, String(sidebarCollapsed.value));
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

  function buildSocketUrl(baseUrl, externalChatId) {
    const url = new URL(baseUrl);
    url.searchParams.set("external_chat_id", externalChatId);
    return url.toString();
  }

  async function requestSettingsJson(pathname, options = {}) {
    const [apiPathname, queryString] = String(pathname).split("?", 2);
    const url = buildHttpApiUrl(state.wsUrl, apiPathname);
    url.search = queryString || "";
    const response = await fetch(url.toString(), {
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

  function getActiveCronSessionId() {
    const session = currentSession.value;
    if (session?.sessionId) {
      return session.sessionId;
    }
    if (session?.externalChatId) {
      return `web:${session.externalChatId}`;
    }
    return "";
  }

  function formatDateTimeLocal(timestampMs) {
    const date = new Date(Number(timestampMs || 0));
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    const offsetMs = date.getTimezoneOffset() * 60_000;
    return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16);
  }

  function resetCronJobForm() {
    settingsState.cronJobForm.sessionId = "";
    settingsState.cronJobForm.jobId = "";
    settingsState.cronJobForm.mode = "cron";
    settingsState.cronJobForm.name = "";
    settingsState.cronJobForm.message = "";
    settingsState.cronJobForm.everySeconds = "3600";
    settingsState.cronJobForm.cronExpr = "0 9 * * *";
    settingsState.cronJobForm.at = "";
    settingsState.cronJobForm.timezone = settingsState.schedule.default_timezone || "UTC";
    settingsState.cronJobForm.deliver = true;
  }

  function buildCronJobPayload() {
    const form = settingsState.cronJobForm;
    const payload = {
      session_id: form.sessionId || getActiveCronSessionId(),
      kind: form.mode,
      name: String(form.name || "").trim(),
      message: String(form.message || "").trim(),
      deliver: Boolean(form.deliver),
    };
    if (form.mode === "every") {
      payload.every_seconds = Number(form.everySeconds);
    } else if (form.mode === "cron") {
      payload.cron_expr = String(form.cronExpr || "").trim();
      payload.tz = String(form.timezone || settingsState.schedule.default_timezone || "UTC").trim();
    } else if (form.mode === "at") {
      payload.at = String(form.at || "").trim();
    }
    return payload;
  }

  function parseLines(value) {
    return String(value || "")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function parseListText(value, fallback = []) {
    const items = String(value || "")
      .replace(/,/g, "\n")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
    return items.length ? items : fallback;
  }

  function parseOptionalJsonObject(value, fieldLabel) {
    const text = String(value || "").trim();
    if (!text) {
      return null;
    }
    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("not object");
      }
      return parsed;
    } catch {
      settingsState.mcpError = copy.value.notices.mcpJsonInvalid(fieldLabel);
      return undefined;
    }
  }

  function formatJsonObject(value) {
    return value && typeof value === "object" && !Array.isArray(value)
      ? JSON.stringify(value, null, 2)
      : "";
  }

  function formatListField(value, fallback = "") {
    if (Array.isArray(value)) {
      return value.map((item) => String(item || "").trim()).filter(Boolean).join("\n");
    }
    if (typeof value === "string") {
      return value.trim();
    }
    return fallback;
  }

  function normalizeMcpTransport(value, fallback = "stdio") {
    const transport = String(value || "").trim();
    if (MCP_TRANSPORT_TYPES.has(transport)) {
      return transport;
    }
    if (["streamable-http", "streamable_http", "http"].includes(transport)) {
      return "streamableHttp";
    }
    return fallback;
  }

  function getMcpServerMap(parsed) {
    if (parsed?.mcpServers && typeof parsed.mcpServers === "object" && !Array.isArray(parsed.mcpServers)) {
      return parsed.mcpServers;
    }
    if (parsed?.mcp_servers && typeof parsed.mcp_servers === "object" && !Array.isArray(parsed.mcp_servers)) {
      return parsed.mcp_servers;
    }
    if (parsed?.servers && typeof parsed.servers === "object" && !Array.isArray(parsed.servers)) {
      return parsed.servers;
    }
    return null;
  }

  function extractMcpServerFromJson(parsed) {
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      settingsState.mcpError = copy.value.notices.mcpJsonInvalid(copy.value.settings.mcp.configJson);
      return null;
    }

    const serverMap = getMcpServerMap(parsed);
    if (serverMap) {
      const entries = Object.entries(serverMap).filter(([, value]) => value && typeof value === "object" && !Array.isArray(value));
      if (entries.length !== 1) {
        settingsState.mcpError = copy.value.notices.mcpJsonSingleServer;
        return null;
      }
      const [serverId, server] = entries[0];
      return { serverId, server };
    }

    if (Array.isArray(parsed.servers)) {
      if (parsed.servers.length !== 1 || !parsed.servers[0] || typeof parsed.servers[0] !== "object") {
        settingsState.mcpError = copy.value.notices.mcpJsonSingleServer;
        return null;
      }
      const server = parsed.servers[0];
      return { serverId: server.id || server.name || server.server_id || server.server_name || "", server };
    }

    if (parsed.server && typeof parsed.server === "object" && !Array.isArray(parsed.server)) {
      return { serverId: parsed.server_name || parsed.server_id || parsed.id || parsed.name || "", server: parsed.server };
    }

    return {
      serverId: parsed.server_id || parsed.serverId || parsed.server_name || parsed.serverName || parsed.id || parsed.name || "",
      server: parsed,
    };
  }

  function normalizeMcpSettings(payload) {
    return {
      ...payload,
      servers: Array.isArray(payload?.servers) ? payload.servers : [],
      runtime: payload?.runtime && typeof payload.runtime === "object"
        ? {
            connected: Boolean(payload.runtime.connected),
            connecting: Boolean(payload.runtime.connecting),
            connect_failures: Number(payload.runtime.connect_failures || 0),
            retry_after: Number(payload.runtime.retry_after || 0),
            tool_names: Array.isArray(payload.runtime.tool_names) ? payload.runtime.tool_names : [],
          }
        : settingsState.mcp.runtime,
    };
  }

  function resetMcpForm() {
    settingsState.mcpForm.showEditor = false;
    settingsState.mcpForm.editingId = "";
    settingsState.mcpForm.serverId = "";
    settingsState.mcpForm.type = "stdio";
    settingsState.mcpForm.command = "";
    settingsState.mcpForm.argsText = "";
    settingsState.mcpForm.url = "";
    settingsState.mcpForm.envJson = "";
    settingsState.mcpForm.headersJson = "";
    settingsState.mcpForm.toolTimeout = "30";
    settingsState.mcpForm.enabledToolsText = "*";
    settingsState.mcpForm.showAdvanced = false;
    settingsState.mcpForm.showJsonInput = false;
    settingsState.mcpForm.jsonText = "";
  }

  function buildMcpServerPayload() {
    settingsState.mcpError = "";
    const form = settingsState.mcpForm;
    const env = parseOptionalJsonObject(form.envJson, copy.value.settings.mcp.env);
    if (env === undefined) {
      return null;
    }
    const headers = parseOptionalJsonObject(form.headersJson, copy.value.settings.mcp.headers);
    if (headers === undefined) {
      return null;
    }

    const payload = {
      server_id: String(form.serverId || "").trim(),
      type: form.type,
      command: String(form.command || "").trim(),
      args: parseLines(form.argsText),
      url: String(form.url || "").trim(),
      tool_timeout: Number(form.toolTimeout || 30),
      enabled_tools: parseListText(form.enabledToolsText, ["*"]),
    };
    if (env !== null) {
      payload.env = env;
    }
    if (headers !== null) {
      payload.headers = headers;
    }
    return payload;
  }

  function makeHistoryMessage(message, index) {
    const metadata = message?.metadata && typeof message.metadata === "object" ? message.metadata : {};
    const role = message?.role === "user" ? "user" : "assistant";
    return {
      id: `history-${normalizeEventTimestamp(message?.created_at)}-${index}-${randomToken()}`,
      role,
      text: String(message?.content || ""),
      meta: metadata.sender_name || metadata.sender_id || (role === "user" ? state.displayName : "OpenSprite"),
      createdAt: normalizeEventTimestamp(message?.created_at),
    };
  }

  function normalizeHistorySession(payload) {
    const sessionId = String(payload?.session_id || "").trim();
    const externalChatId = String(payload?.external_chat_id || "").trim()
      || externalChatIdFromSessionId(sessionId)
      || generateExternalChatId();
    const session = createSession(externalChatId);
    session.sessionId = sessionId || null;
    session.title = String(payload?.title || "").trim() || "New chat";
    session.updatedAt = normalizeEventTimestamp(payload?.updated_at);
    session.messages = Array.isArray(payload?.messages)
      ? payload.messages.map(makeHistoryMessage).filter((message) => message.text.trim())
      : [];
    return session;
  }

  function mergeHistorySessions(historySessions) {
    if (!historySessions.length) {
      return;
    }

    const sessionsByExternalChatId = new Map(historySessions.map((session) => [session.externalChatId, session]));
    for (const session of state.sessions) {
      if (!sessionsByExternalChatId.has(session.externalChatId) && (session.sessionId || session.messages.length > 0)) {
        sessionsByExternalChatId.set(session.externalChatId, session);
      }
    }

    state.sessions = [...sessionsByExternalChatId.values()].sort((left, right) => right.updatedAt - left.updatedAt);
    if (!state.sessions.some((session) => session.externalChatId === state.activeExternalChatId)) {
      state.activeExternalChatId = state.sessions[0]?.externalChatId || state.activeExternalChatId;
      writeStoredValue(STORAGE_KEYS.activeExternalChatId, state.activeExternalChatId);
    }
  }

  async function loadSessionHistory() {
    try {
      const payload = await requestSettingsJson("/api/sessions?limit=50&messages=50");
      const historySessions = Array.isArray(payload.sessions)
        ? payload.sessions.map(normalizeHistorySession)
        : [];
      mergeHistorySessions(historySessions);
    } catch {
      setNotice(copy.value.notices.historyLoadFailed, "warning");
    }
  }

  async function loadProviderSettings() {
    settingsState.providersLoading = true;
    settingsState.providersError = "";
    try {
      settingsState.providers = await requestSettingsJson("/api/settings/providers");
    } catch (error) {
      settingsState.providersError = error?.message || copy.value.notices.providerLoadFailed;
    } finally {
      settingsState.providersLoading = false;
    }
  }

  function visibleChannels(channels) {
    return (channels || []).filter((channel) => channel.id !== "web" && channel.id !== "console");
  }

  function normalizeChannelSettings(payload) {
    const channels = visibleChannels(payload.channels);
    const hasGroupedChannels = Array.isArray(payload.connected) || Array.isArray(payload.available);
    if (hasGroupedChannels) {
      return {
        ...payload,
        connected: visibleChannels(payload.connected),
        available: visibleChannels(payload.available),
        channels,
      };
    }

    return {
      ...payload,
      connected: channels.filter((channel) => channel.token_configured),
      available: channels.filter((channel) => !channel.token_configured),
      channels,
    };
  }

  function sortChannelList(channels) {
    return [...channels].sort((left, right) => String(left.name || left.id).localeCompare(String(right.name || right.id)));
  }

  function upsertConnectedChannel(channel) {
    const visibleChannel = visibleChannels([channel])[0];
    if (!visibleChannel) {
      return;
    }
    const connected = settingsState.channels.connected.filter((entry) => entry.id !== visibleChannel.id);
    const nextConnected = sortChannelList([...connected, visibleChannel]);
    settingsState.channels = {
      ...settingsState.channels,
      connected: nextConnected,
      channels: nextConnected,
    };
  }

  function removeConnectedChannel(channelId) {
    const nextConnected = settingsState.channels.connected.filter((entry) => entry.id !== channelId);
    settingsState.channels = {
      ...settingsState.channels,
      connected: nextConnected,
      channels: nextConnected,
    };
  }

  async function loadChannelSettings() {
    settingsState.channelsLoading = true;
    settingsState.channelsError = "";
    try {
      const payload = await requestSettingsJson("/api/settings/channels");
      settingsState.channels = normalizeChannelSettings(payload);
    } catch (error) {
      settingsState.channelsError = error?.message || copy.value.notices.channelLoadFailed;
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
      settingsState.modelsError = error?.message || copy.value.notices.modelLoadFailed;
    } finally {
      settingsState.modelsLoading = false;
    }
  }

  async function loadMcpSettings() {
    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    try {
      settingsState.mcp = normalizeMcpSettings(await requestSettingsJson("/api/settings/mcp"));
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpLoadFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  async function loadScheduleSettings() {
    settingsState.scheduleLoading = true;
    settingsState.scheduleError = "";
    try {
      const payload = await requestSettingsJson("/api/settings/schedule");
      settingsState.schedule = payload;
      settingsState.scheduleForm.defaultTimezone = payload.default_timezone || "UTC";
      if (!settingsState.cronJobForm.timezone || !settingsState.cronJobForm.jobId) {
        settingsState.cronJobForm.timezone = settingsState.scheduleForm.defaultTimezone;
      }
    } catch (error) {
      settingsState.scheduleError = error?.message || copy.value.notices.scheduleLoadFailed;
    } finally {
      settingsState.scheduleLoading = false;
    }
  }

  async function loadCronJobs() {
    settingsState.cronJobsLoading = true;
    settingsState.cronJobsError = "";
    try {
      const payload = await requestSettingsJson("/api/cron/jobs");
      settingsState.cronJobs = Array.isArray(payload.jobs) ? payload.jobs : [];
    } catch (error) {
      settingsState.cronJobsError = error?.message || copy.value.notices.cronJobsLoadFailed;
    } finally {
      settingsState.cronJobsLoading = false;
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
      return;
    }
    if (sectionName === "mcp") {
      loadMcpSettings();
      return;
    }
    if (sectionName === "schedule") {
      loadScheduleSettings();
      loadCronJobs();
    }
  }

  function beginChannelConnect(channel) {
    settingsState.channelsNotice = "";
    settingsState.channelsError = "";
    cancelProviderConnect();
    settingsState.channelConnectForm.type = channel.type || channel.id;
    settingsState.channelConnectForm.name = channel.name || "";
    settingsState.channelConnectForm.token = "";
  }

  function cancelChannelConnect() {
    settingsState.channelConnectForm.type = "";
    settingsState.channelConnectForm.name = "";
    settingsState.channelConnectForm.token = "";
  }

  async function saveChannelConnection() {
    const channelType = settingsState.channelConnectForm.type;
    if (!channelType) {
      return;
    }
    settingsState.channelsLoading = true;
    settingsState.channelsError = "";
    settingsState.channelsNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/channels", {
        method: "POST",
        body: JSON.stringify({
          type: channelType,
          name: settingsState.channelConnectForm.name,
          token: settingsState.channelConnectForm.token,
        }),
      });
      settingsState.channelsNotice = copy.value.notices.channelConnected(payload.channel.name, payload.restart_required);
      upsertConnectedChannel(payload.channel);
      cancelChannelConnect();
      await loadChannelSettings();
    } catch (error) {
      settingsState.channelsError = error?.message || copy.value.notices.channelConnectFailed;
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
      settingsState.channelsNotice = copy.value.notices.channelDisconnected(channel.name, payload.restart_required);
      removeConnectedChannel(channel.id);
      await loadChannelSettings();
    } catch (error) {
      settingsState.channelsError = error?.message || copy.value.notices.channelDisconnectFailed;
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
      settingsState.providersNotice = copy.value.notices.providerConnected;
      cancelProviderConnect();
      await loadProviderSettings();
      await loadModelSettings();
    } catch (error) {
      settingsState.providersError = error?.message || copy.value.notices.providerConnectFailed;
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
      settingsState.providersNotice = copy.value.notices.providerDisconnected(provider.name, payload.restart_required);
      await loadProviderSettings();
      await loadModelSettings();
    } catch (error) {
      settingsState.providersError = error?.message || copy.value.notices.providerDisconnectFailed;
    } finally {
      settingsState.providersLoading = false;
    }
  }

  async function selectModel(providerId, model) {
    const normalizedModel = String(model || "").trim();
    if (!normalizedModel) {
      settingsState.modelsError = copy.value.notices.modelRequired;
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
        ? copy.value.notices.modelRestartRequired
        : copy.value.notices.modelApplied;
      settingsState.customModels[providerId] = "";
      await loadModelSettings();
      await loadProviderSettings();
    } catch (error) {
      settingsState.modelsError = error?.message || copy.value.notices.modelSelectFailed;
    } finally {
      settingsState.modelsLoading = false;
    }
  }

  function beginMcpEdit(server) {
    settingsState.mcpNotice = "";
    settingsState.mcpError = "";
    settingsState.mcpForm.showEditor = true;
    settingsState.mcpForm.editingId = server.id;
    settingsState.mcpForm.serverId = server.id;
    settingsState.mcpForm.type = server.type || "stdio";
    settingsState.mcpForm.command = server.command || "";
    settingsState.mcpForm.argsText = Array.isArray(server.args) ? server.args.join("\n") : "";
    settingsState.mcpForm.url = server.url || "";
    settingsState.mcpForm.envJson = "";
    settingsState.mcpForm.headersJson = "";
    settingsState.mcpForm.toolTimeout = String(server.tool_timeout || 30);
    settingsState.mcpForm.enabledToolsText = Array.isArray(server.enabled_tools) ? server.enabled_tools.join("\n") : "*";
    settingsState.mcpForm.showAdvanced = false;
    settingsState.mcpForm.showJsonInput = false;
    settingsState.mcpForm.jsonText = "";
  }

  function cancelMcpEdit() {
    resetMcpForm();
  }

  function beginMcpCreate() {
    resetMcpForm();
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    settingsState.mcpForm.showEditor = true;
  }

  function toggleMcpAdvanced() {
    settingsState.mcpForm.showAdvanced = !settingsState.mcpForm.showAdvanced;
  }

  function toggleMcpJsonInput() {
    settingsState.mcpForm.showJsonInput = !settingsState.mcpForm.showJsonInput;
  }

  function applyMcpJson() {
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    let parsed;
    try {
      parsed = JSON.parse(String(settingsState.mcpForm.jsonText || ""));
    } catch {
      settingsState.mcpError = copy.value.notices.mcpJsonInvalid(copy.value.settings.mcp.configJson);
      return;
    }

    const extracted = extractMcpServerFromJson(parsed);
    if (!extracted) {
      return;
    }

    const form = settingsState.mcpForm;
    const server = extracted.server;
    const nextServerId = String(extracted.serverId || "").trim();
    if (form.editingId && nextServerId && nextServerId !== form.editingId) {
      settingsState.mcpError = copy.value.notices.mcpJsonEditingMismatch;
      return;
    }
    if (!form.editingId && nextServerId) {
      form.serverId = nextServerId;
    }

    const rawType = server.type || server.transport_type || server.transport;
    form.type = normalizeMcpTransport(rawType, server.url ? "streamableHttp" : "stdio");
    form.command = String(server.command || "").trim();
    form.argsText = formatListField(server.args, form.argsText);
    form.url = String(server.url || "").trim();
    form.toolTimeout = String(server.tool_timeout || server.toolTimeout || form.toolTimeout || 30);
    form.enabledToolsText = formatListField(server.enabled_tools || server.enabledTools, form.enabledToolsText || "*") || "*";
    form.envJson = formatJsonObject(server.env) || form.envJson;
    form.headersJson = formatJsonObject(server.headers) || form.headersJson;
    form.showAdvanced = Boolean(server.env || server.headers || server.tool_timeout || server.toolTimeout || server.enabled_tools || server.enabledTools);
    form.showJsonInput = false;
    settingsState.mcpNotice = copy.value.notices.mcpJsonApplied;
  }

  async function saveMcpServer() {
    const payload = buildMcpServerPayload();
    if (payload === null) {
      return;
    }
    if (!payload.server_id) {
      settingsState.mcpError = copy.value.notices.mcpServerIdRequired;
      return;
    }
    if (payload.type === "stdio" && !payload.command) {
      settingsState.mcpError = copy.value.notices.mcpCommandRequired;
      return;
    }
    if ((payload.type === "sse" || payload.type === "streamableHttp") && !payload.url) {
      settingsState.mcpError = copy.value.notices.mcpUrlRequired;
      return;
    }

    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    try {
      const editingId = settingsState.mcpForm.editingId;
      const response = await requestSettingsJson(editingId ? `/api/settings/mcp/${encodeURIComponent(editingId)}` : "/api/settings/mcp", {
        method: editingId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      settingsState.mcp = normalizeMcpSettings(response);
      settingsState.mcpNotice = response.reload_message || copy.value.notices.mcpSaved;
      resetMcpForm();
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpSaveFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  async function removeMcpServer(server) {
    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    try {
      const response = await requestSettingsJson(`/api/settings/mcp/${encodeURIComponent(server.id)}`, {
        method: "DELETE",
      });
      settingsState.mcp = normalizeMcpSettings(response);
      settingsState.mcpNotice = response.reload_message || copy.value.notices.mcpRemoved;
      if (settingsState.mcpForm.editingId === server.id) {
        resetMcpForm();
      }
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpRemoveFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  async function reloadMcpSettings() {
    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    try {
      const response = await requestSettingsJson("/api/settings/mcp/reload", { method: "POST" });
      settingsState.mcp = normalizeMcpSettings(response);
      settingsState.mcpNotice = response.reload_message || copy.value.notices.mcpReloaded;
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpReloadFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  async function saveScheduleSettings() {
    const defaultTimezone = String(settingsState.scheduleForm.defaultTimezone || "").trim() || "UTC";
    settingsState.scheduleLoading = true;
    settingsState.scheduleError = "";
    settingsState.scheduleNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/schedule", {
        method: "PUT",
        body: JSON.stringify({ default_timezone: defaultTimezone }),
      });
      settingsState.schedule = payload;
      settingsState.scheduleForm.defaultTimezone = payload.default_timezone || defaultTimezone;
      settingsState.scheduleNotice = payload.restart_required
        ? copy.value.notices.scheduleRestartRequired
        : copy.value.notices.scheduleSaved(settingsState.scheduleForm.defaultTimezone);
    } catch (error) {
      settingsState.scheduleError = error?.message || copy.value.notices.scheduleSaveFailed;
    } finally {
      settingsState.scheduleLoading = false;
    }
  }

  function beginCronJobEdit(job) {
    const schedule = job?.schedule || {};
    const payload = job?.payload || {};
    settingsState.cronJobsNotice = "";
    settingsState.cronJobsError = "";
    settingsState.cronJobForm.sessionId = job?.session_id || "";
    settingsState.cronJobForm.jobId = job?.id || "";
    settingsState.cronJobForm.mode = schedule.kind || "cron";
    settingsState.cronJobForm.name = job?.name || "";
    settingsState.cronJobForm.message = payload.message || "";
    settingsState.cronJobForm.everySeconds = schedule.every_ms ? String(Math.max(1, Math.floor(schedule.every_ms / 1000))) : "3600";
    settingsState.cronJobForm.cronExpr = schedule.expr || "0 9 * * *";
    settingsState.cronJobForm.at = schedule.at_ms ? formatDateTimeLocal(schedule.at_ms) : "";
    settingsState.cronJobForm.timezone = schedule.tz || settingsState.schedule.default_timezone || "UTC";
    settingsState.cronJobForm.deliver = payload.deliver !== false;
  }

  function cancelCronJobEdit() {
    resetCronJobForm();
  }

  async function saveCronJob() {
    const payload = buildCronJobPayload();
    if (!payload.session_id) {
      settingsState.cronJobsError = copy.value.notices.sessionNotReady;
      return;
    }
    if (!payload.message) {
      settingsState.cronJobsError = copy.value.notices.cronJobMessageRequired;
      return;
    }

    const jobId = settingsState.cronJobForm.jobId;
    settingsState.cronJobsLoading = true;
    settingsState.cronJobsError = "";
    settingsState.cronJobsNotice = "";
    try {
      await requestSettingsJson(jobId ? `/api/cron/jobs/${encodeURIComponent(jobId)}` : "/api/cron/jobs", {
        method: jobId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      settingsState.cronJobsNotice = jobId ? copy.value.notices.cronJobUpdated : copy.value.notices.cronJobCreated;
      resetCronJobForm();
      await loadCronJobs();
    } catch (error) {
      settingsState.cronJobsError = error?.message || copy.value.notices.cronJobSaveFailed;
    } finally {
      settingsState.cronJobsLoading = false;
    }
  }

  async function runCronJobAction(job, action) {
    const sessionId = job?.session_id || getActiveCronSessionId();
    if (!sessionId) {
      settingsState.cronJobsError = copy.value.notices.sessionNotReady;
      return;
    }

    settingsState.cronJobsLoading = true;
    settingsState.cronJobsError = "";
    settingsState.cronJobsNotice = "";
    try {
      if (action === "remove") {
        await requestSettingsJson(`/api/cron/jobs/${encodeURIComponent(job.id)}?session_id=${encodeURIComponent(sessionId)}`, {
          method: "DELETE",
        });
      } else {
        await requestSettingsJson(`/api/cron/jobs/${encodeURIComponent(job.id)}/${encodeURIComponent(action)}`, {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId }),
        });
      }
      settingsState.cronJobsNotice = copy.value.notices.cronJobActionDone;
      await loadCronJobs();
    } catch (error) {
      settingsState.cronJobsError = error?.message || copy.value.notices.cronJobActionFailed;
    } finally {
      settingsState.cronJobsLoading = false;
    }
  }

  function handleSocketMessage(rawData) {
    let payload;
    try {
      payload = JSON.parse(rawData);
    } catch {
      setNotice(copy.value.notices.parseError, "error");
      return;
    }

    if (payload.type === "session") {
      const session = ensureSession(payload.external_chat_id, payload.session_id);
      if (!state.activeExternalChatId) {
        state.activeExternalChatId = session.externalChatId;
      }
      persistActiveSession();
      setNotice(copy.value.notices.liveSessionReady(payload.session_id), "success");
      return;
    }

    if (payload.type === "message") {
      const externalChatId = payload.external_chat_id || currentSession.value?.externalChatId || generateExternalChatId();
      const session = ensureSession(externalChatId, payload.session_id);
      addMessage(session.externalChatId, makeMessage("assistant", payload.text || "", payload.session_id || "OpenSprite"));
      scrollMessagesToBottom();
      return;
    }

    if (payload.type === "run_event") {
      handleRunEvent(payload);
      scrollMessagesToBottom();
      return;
    }

    if (payload.type === "error") {
      setNotice(payload.error || copy.value.notices.gatewayError, "error");
    }
  }

  function connectSocket() {
    const session = currentSession.value;
    if (!session) {
      return;
    }

    let socketUrl;
    try {
      socketUrl = buildSocketUrl(state.wsUrl, session.externalChatId);
    } catch {
      setNotice(copy.value.notices.invalidWs, "error");
      openSettings("general");
      return;
    }

    if (activeSocket) {
      disconnectSocket(copy.value.notices.refreshConnection, "info");
    }

    state.connectionState = "connecting";
    setNotice(copy.value.notices.connectingTo(state.wsUrl), "info");

    const socket = new WebSocket(socketUrl);
    activeSocket = socket;

    socket.addEventListener("open", () => {
      if (activeSocket !== socket) {
        return;
      }
      state.connectionState = "connected";
      setNotice(copy.value.notices.connected, "success");
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
      setNotice(copy.value.notices.socketFailed, "error");
    });

    socket.addEventListener("close", () => {
      if (activeSocket !== socket) {
        return;
      }
      const failedToConnect = state.connectionState === "connecting";
      activeSocket = null;
      state.connectionState = "disconnected";
      setNotice(
        failedToConnect ? copy.value.notices.couldNotConnect : copy.value.notices.disconnected,
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
    state.activeExternalChatId = session.externalChatId;
    writeStoredValue(STORAGE_KEYS.activeExternalChatId, session.externalChatId);
    setNotice(copy.value.notices.newDraft, "info");
    scrollMessagesToBottom();
  }

  function saveConnectionSettings() {
    const nextWsUrl = settingsForm.wsUrl.trim() || DEFAULT_WS_URL;
    const shouldReconnect = state.wsUrl !== nextWsUrl && activeSocket && state.connectionState !== "disconnected";

    state.wsUrl = nextWsUrl;
    state.displayName = settingsForm.displayName.trim() || "Local browser";
    saveRunPanelVisibilitySettings(settingsForm.showRunTimeline, settingsForm.showRunTrace);

    const requestedExternalChatId = settingsForm.externalChatId.trim();
    if (requestedExternalChatId) {
      ensureSession(requestedExternalChatId);
      state.activeExternalChatId = requestedExternalChatId;
    } else {
      const session = createSession();
      state.sessions.unshift(session);
      state.activeExternalChatId = session.externalChatId;
      settingsForm.externalChatId = session.externalChatId;
    }

    writeStoredValue(STORAGE_KEYS.wsUrl, state.wsUrl);
    writeStoredValue(STORAGE_KEYS.displayName, state.displayName);
    writeStoredValue(STORAGE_KEYS.activeExternalChatId, state.activeExternalChatId);
    settingsForm.wsUrl = state.wsUrl;
    settingsForm.displayName = state.displayName;
    settingsForm.externalChatId = state.activeExternalChatId;

    if (shouldReconnect) {
      connectSocket();
    }
  }

  function toggleSettingsConnection(shouldConnect) {
    if (shouldConnect) {
      saveConnectionSettings();
      connectSocket();
      return;
    }
    disconnectSocket(copy.value.notices.disconnected, "warning");
  }

  async function cancelRun(run) {
    const session = currentSession.value;
    if (!session || !run?.runId || run.status !== "running") {
      return;
    }
    const sessionId = getSessionApiId(session);
    if (!sessionId) {
      setNotice(copy.value.notices.sessionNotReady, "warning");
      return;
    }

    run.cancelPending = true;
    try {
      const response = await fetch(buildRunCancelUrl(state.wsUrl, run.runId, sessionId), { method: "POST" });
      if (!response.ok) {
        throw new Error(`Cancel request failed with HTTP ${response.status}`);
      }
      setNotice(copy.value.notices.cancelRequested(run.runId), "warning");
    } catch (error) {
      setNotice(error?.message || copy.value.notices.cancelFailed, "error");
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
        setNotice(copy.value.notices.stillConnecting, "info");
        return;
      }
      setNotice(copy.value.notices.inactiveConnection, "warning");
      openSettings("general");
      return;
    }

    const session = currentSession.value;
    if (!session) {
      return;
    }

    addMessage(session.externalChatId, makeMessage("user", text, state.displayName || "Local browser"));
    activeSocket.send(
      JSON.stringify({
        external_chat_id: session.externalChatId,
        ...(session.sessionId ? { session_id: session.sessionId } : {}),
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

  async function initializeClient() {
    await loadSessionHistory();
    if (clientDisposed) {
      return;
    }
    persistActiveSession();
    connectSocket();
  }

  function handleGlobalKeydown(event) {
    const pressedSettingsShortcut = event.key === "," && (event.ctrlKey || event.metaKey);
    if (pressedSettingsShortcut) {
      event.preventDefault();
      openSettings("general");
      return;
    }

    if (event.key === "Escape") {
      closeSettings();
      closeSidebar();
    }
  }

  onMounted(() => {
    addColorSchemeListener();
    applyDocumentPreferences();
    document.addEventListener("keydown", handleGlobalKeydown);
    resizeComposer();
    scrollMessagesToBottom();
    initializeClient();
  });

  onBeforeUnmount(() => {
    clientDisposed = true;
    removeColorSchemeListener();
    document.removeEventListener("keydown", handleGlobalKeydown);
    document.body.classList.remove("settings-open", "sidebar-open");
    if (activeSocket && activeSocket.readyState !== WebSocket.CLOSED) {
      activeSocket.close(1000, "Client disconnect");
    }
    activeSocket = null;
  });

  return {
    copy,
    prompts,
    state,
    messageText,
    messageInput,
    messageStage,
    sidebarOpen,
    sidebarCollapsed,
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
    getSessionTitle,
    setActiveSession,
    selectSettingsSection,
    openSettings,
    closeSettings,
    saveConnectionSettings,
    loadProviderSettings,
    loadModelSettings,
    loadChannelSettings,
    loadScheduleSettings,
    loadMcpSettings,
    loadCronJobs,
    beginChannelConnect,
    cancelChannelConnect,
    saveChannelConnection,
    disconnectChannel,
    beginProviderConnect,
    cancelProviderConnect,
    saveProviderConnection,
    disconnectProvider,
    selectModel,
    beginMcpEdit,
    beginMcpCreate,
    cancelMcpEdit,
    saveMcpServer,
    removeMcpServer,
    reloadMcpSettings,
    toggleMcpAdvanced,
    toggleMcpJsonInput,
    applyMcpJson,
    saveScheduleSettings,
    beginCronJobEdit,
    cancelCronJobEdit,
    saveCronJob,
    runCronJobAction,
    toggleSidebar,
    toggleSidebarCollapsed,
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
