const STORAGE_KEYS = {
  wsUrl: "opensprite:web:wsUrl",
  displayName: "opensprite:web:displayName",
  activeChatId: "opensprite:web:activeChatId",
};

function resolveDefaultWsUrl() {
  if (window.location.protocol === "http:" || window.location.protocol === "https:") {
    const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProtocol}//${window.location.host}/ws`;
  }
  return "ws://127.0.0.1:8765/ws";
}

const DEFAULT_WS_URL = resolveDefaultWsUrl();

const dom = {
  sessionList: document.getElementById("sessionList"),
  sessionCount: document.getElementById("sessionCount"),
  sessionMeta: document.getElementById("sessionMeta"),
  messageList: document.getElementById("messageList"),
  messageStage: document.getElementById("messageStage"),
  noticeBanner: document.getElementById("noticeBanner"),
  connectionState: document.getElementById("connectionState"),
  statusDot: document.getElementById("statusDot"),
  runtimeHint: document.getElementById("runtimeHint"),
  connectButton: document.getElementById("connectButton"),
  newChatButton: document.getElementById("newChatButton"),
  composerForm: document.getElementById("composerForm"),
  messageInput: document.getElementById("messageInput"),
  sendButton: document.getElementById("sendButton"),
  mobileNavToggle: document.getElementById("mobileNavToggle"),
  settingsModal: document.getElementById("settingsModal"),
  settingsToggle: document.getElementById("settingsToggle"),
  settingsClose: document.getElementById("settingsClose"),
  settingsBackdrop: document.getElementById("settingsBackdrop"),
  saveSettingsButton: document.getElementById("saveSettingsButton"),
  disconnectButton: document.getElementById("disconnectButton"),
  wsUrlInput: document.getElementById("wsUrlInput"),
  displayNameInput: document.getElementById("displayNameInput"),
  chatIdInput: document.getElementById("chatIdInput"),
  settingsTitle: document.getElementById("settingsTitle"),
  settingsTabs: document.querySelectorAll("[data-settings-section]"),
  settingsPages: document.querySelectorAll("[data-settings-page]"),
  emptyState: document.getElementById("emptyState"),
  promptCards: document.querySelectorAll("[data-prompt]"),
};

const SETTINGS_TITLES = {
  general: "一般",
  shortcuts: "快速鍵",
  providers: "提供者",
  models: "模型",
};

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

function createIntroMessages() {
  return [];
}

function createSession(chatId) {
  return {
    chatId: chatId || generateChatId(),
    sessionChatId: null,
    title: "New chat",
    updatedAt: Date.now(),
    messages: createIntroMessages(),
  };
}

const storedChatId = readStoredValue(STORAGE_KEYS.activeChatId, "");
const initialSession = createSession(storedChatId || generateChatId());

const state = {
  wsUrl: readStoredValue(STORAGE_KEYS.wsUrl, DEFAULT_WS_URL),
  displayName: readStoredValue(STORAGE_KEYS.displayName, "Local browser"),
  activeChatId: initialSession.chatId,
  sessions: [initialSession],
  socket: null,
  connectionState: "disconnected",
  notice: {
    text: "Connecting to your local OpenSprite gateway...",
    tone: "info",
  },
};

function sortSessions() {
  state.sessions.sort((left, right) => right.updatedAt - left.updatedAt);
}

function getCurrentSession() {
  return state.sessions.find((session) => session.chatId === state.activeChatId) || null;
}

function getSessionDisplayId(session) {
  if (!session) {
    return "No active chat";
  }
  return session.sessionChatId || `web:${session.chatId}`;
}

function ensureSession(chatId, sessionChatId) {
  let session = state.sessions.find((entry) => entry.chatId === chatId);
  if (!session) {
    session = createSession(chatId);
    session.messages = [
      makeMessage(
        "assistant",
        "This thread was created from the live gateway. Send a message to continue the conversation.",
        "OpenSprite"
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

function setNotice(text, tone) {
  state.notice = { text, tone };
}

function setActiveSession(chatId) {
  state.activeChatId = chatId;
  writeStoredValue(STORAGE_KEYS.activeChatId, chatId);
  closeSidebar();
  render();
}

function selectSettingsSection(sectionName) {
  const nextSection = SETTINGS_TITLES[sectionName] ? sectionName : "general";
  dom.settingsTabs.forEach((button) => {
    button.classList.toggle("settings-nav__item--active", button.dataset.settingsSection === nextSection);
  });
  dom.settingsPages.forEach((page) => {
    page.hidden = page.dataset.settingsPage !== nextSection;
  });
  dom.settingsTitle.textContent = SETTINGS_TITLES[nextSection];
}

function openSettings(sectionName = "general") {
  dom.settingsModal.hidden = false;
  document.body.classList.add("settings-open");
  selectSettingsSection(sectionName);
  renderSettings();
}

function closeSettings() {
  dom.settingsModal.hidden = true;
  document.body.classList.remove("settings-open");
}

function openSidebar() {
  document.body.classList.add("sidebar-open");
  dom.mobileNavToggle.setAttribute("aria-expanded", "true");
}

function closeSidebar() {
  document.body.classList.remove("sidebar-open");
  dom.mobileNavToggle.setAttribute("aria-expanded", "false");
}

function disconnectSocket(reason, tone = "warning") {
  const activeSocket = state.socket;
  state.socket = null;
  state.connectionState = "disconnected";
  if (activeSocket) {
    activeSocket.close(1000, "Client disconnect");
  }
  setNotice(reason, tone);
  render();
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
    render();
    return;
  }

  if (payload.type === "session") {
    const session = ensureSession(payload.chat_id, payload.session_chat_id);
    if (!state.activeChatId) {
      state.activeChatId = session.chatId;
    }
    setNotice(`Live session ready: ${payload.session_chat_id}`, "success");
    render();
    return;
  }

  if (payload.type === "message") {
    const chatId = payload.chat_id || getCurrentSession()?.chatId || generateChatId();
    const session = ensureSession(chatId, payload.session_chat_id);
    addMessage(
      session.chatId,
      makeMessage("assistant", payload.text || "", payload.session_chat_id || "OpenSprite")
    );
    render();
    scrollMessagesToBottom();
    return;
  }

  if (payload.type === "error") {
    setNotice(payload.error || "The gateway returned an error.", "error");
    render();
  }
}

function connectSocket() {
  const currentSession = getCurrentSession();
  if (!currentSession) {
    return;
  }

  let socketUrl;
  try {
    socketUrl = buildSocketUrl(state.wsUrl, currentSession.chatId);
  } catch {
    setNotice("The WebSocket URL is invalid. Check it in settings first.", "error");
    render();
    openSettings("providers");
    return;
  }

  if (state.socket) {
    disconnectSocket("Refreshing the connection...", "info");
  }

  state.connectionState = "connecting";
  setNotice(`Connecting to ${state.wsUrl}`, "info");
  render();

  const socket = new WebSocket(socketUrl);
  state.socket = socket;

  socket.addEventListener("open", () => {
    if (state.socket !== socket) {
      return;
    }
    state.connectionState = "connected";
    setNotice("Connected. Send a message to talk to your local gateway.", "success");
    render();
  });

  socket.addEventListener("message", (event) => {
    if (state.socket !== socket) {
      return;
    }
    handleSocketMessage(event.data);
  });

  socket.addEventListener("error", () => {
    if (state.socket !== socket) {
      return;
    }
    setNotice("The WebSocket connection failed. Make sure `opensprite gateway` is running.", "error");
    render();
  });

  socket.addEventListener("close", () => {
    if (state.socket !== socket) {
      return;
    }
    const failedToConnect = state.connectionState === "connecting";
    state.socket = null;
    state.connectionState = "disconnected";
    setNotice(
      failedToConnect
        ? "Could not connect. Start the gateway, then try again."
        : "Disconnected from the gateway.",
      failedToConnect ? "error" : "warning"
    );
    render();
  });
}

function resizeComposer() {
  dom.messageInput.style.height = "auto";
  dom.messageInput.style.height = `${Math.min(dom.messageInput.scrollHeight, 220)}px`;
}

function scrollMessagesToBottom() {
  requestAnimationFrame(() => {
    dom.messageStage.scrollTop = dom.messageStage.scrollHeight;
  });
}

function renderSessionList() {
  sortSessions();
  dom.sessionCount.textContent = String(state.sessions.length);
  dom.sessionList.innerHTML = "";

  state.sessions.forEach((session) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "session-tile";
    if (session.chatId === state.activeChatId) {
      button.classList.add("session-tile--active");
    }

    const title = document.createElement("strong");
    title.textContent = session.title;
    const subtitle = document.createElement("span");
    subtitle.textContent = getSessionDisplayId(session);

    button.append(title, subtitle);
    button.addEventListener("click", () => setActiveSession(session.chatId));
    dom.sessionList.appendChild(button);
  });
}

function renderMessages() {
  const currentSession = getCurrentSession();
  dom.messageList.innerHTML = "";
  if (!currentSession) {
    dom.emptyState.hidden = false;
    return;
  }

  dom.emptyState.hidden = currentSession.messages.length > 0;

  currentSession.messages.forEach((message) => {
    const article = document.createElement("article");
    article.className = `message message--${message.role}`;

    const avatar = document.createElement("div");
    avatar.className = "message__avatar";
    avatar.textContent = message.role === "user" ? "You" : "OS";

    const content = document.createElement("div");
    content.className = "message__content";

    const meta = document.createElement("div");
    meta.className = "message__meta";
    meta.textContent = message.meta || (message.role === "user" ? state.displayName : "OpenSprite");

    const bubble = document.createElement("div");
    bubble.className = "message__bubble";
    bubble.textContent = message.text;

    content.append(meta, bubble);
    article.append(avatar, content);
    dom.messageList.appendChild(article);
  });
}

function renderStatus() {
  const currentSession = getCurrentSession();
  const chatId = currentSession?.chatId || "No active chat";

  dom.sessionMeta.textContent = `${currentSession?.title || "New chat"} · ${getSessionDisplayId(currentSession)}`;
  dom.runtimeHint.textContent = chatId;

  const labels = {
    disconnected: "Disconnected",
    connecting: "Connecting",
    connected: "Connected",
  };
  dom.connectionState.textContent = labels[state.connectionState] || "Disconnected";
  dom.statusDot.className = "status-dot";
  if (state.connectionState === "connected") {
    dom.statusDot.classList.add("status-dot--connected");
  }
  if (state.connectionState === "connecting") {
    dom.statusDot.classList.add("status-dot--connecting");
  }

  const connectLabels = {
    disconnected: "Retry",
    connecting: "Connecting",
    connected: "Reconnect",
  };
  dom.connectButton.textContent = connectLabels[state.connectionState] || "Retry";
  dom.connectButton.disabled = state.connectionState === "connecting";
  dom.sendButton.disabled = state.connectionState !== "connected";

  dom.noticeBanner.hidden = !state.notice.text;
  dom.noticeBanner.dataset.tone = state.notice.tone || "info";
  dom.noticeBanner.textContent = state.notice.text;
}

function renderSettings() {
  const currentSession = getCurrentSession();
  dom.wsUrlInput.value = state.wsUrl;
  dom.displayNameInput.value = state.displayName;
  dom.chatIdInput.value = currentSession?.chatId || "";
}

function render() {
  renderSessionList();
  renderMessages();
  renderStatus();
  renderSettings();
}

function createNewChat() {
  const session = createSession();
  state.sessions.unshift(session);
  state.activeChatId = session.chatId;
  writeStoredValue(STORAGE_KEYS.activeChatId, session.chatId);
  setNotice("Started a fresh local draft. Your next live message will use a new chat ID.", "info");
  render();
  scrollMessagesToBottom();
}

function saveSettingsAndConnect() {
  state.wsUrl = dom.wsUrlInput.value.trim() || DEFAULT_WS_URL;
  state.displayName = dom.displayNameInput.value.trim() || "Local browser";

  const requestedChatId = dom.chatIdInput.value.trim();
  if (requestedChatId) {
    ensureSession(requestedChatId);
    state.activeChatId = requestedChatId;
  }

  writeStoredValue(STORAGE_KEYS.wsUrl, state.wsUrl);
  writeStoredValue(STORAGE_KEYS.displayName, state.displayName);
  writeStoredValue(STORAGE_KEYS.activeChatId, state.activeChatId);

  closeSettings();
  render();
  connectSocket();
}

function submitMessage(event) {
  event.preventDefault();
  const text = dom.messageInput.value.trim();
  if (!text) {
    return;
  }

  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    if (state.connectionState === "connecting") {
      setNotice("Still connecting to the local gateway. Your message can be sent once the status turns connected.", "info");
      render();
      return;
    }
    setNotice("The automatic connection is not active. Check the endpoint, then retry.", "warning");
    render();
    openSettings("providers");
    return;
  }

  const currentSession = getCurrentSession();
  if (!currentSession) {
    return;
  }

  addMessage(currentSession.chatId, makeMessage("user", text, state.displayName || "Local browser"));
  state.socket.send(
    JSON.stringify({
      chat_id: currentSession.chatId,
      sender_name: state.displayName,
      text,
    })
  );

  dom.messageInput.value = "";
  resizeComposer();
  render();
  scrollMessagesToBottom();
}

dom.newChatButton.addEventListener("click", createNewChat);
dom.connectButton.addEventListener("click", connectSocket);
dom.composerForm.addEventListener("submit", submitMessage);
dom.messageInput.addEventListener("input", resizeComposer);
dom.messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    submitMessage(event);
  }
});
dom.promptCards.forEach((button) => {
  button.addEventListener("click", () => {
    dom.messageInput.value = button.dataset.prompt || "";
    resizeComposer();
    dom.messageInput.focus();
  });
});

dom.settingsToggle.addEventListener("click", () => openSettings());
dom.settingsTabs.forEach((button) => {
  button.addEventListener("click", () => selectSettingsSection(button.dataset.settingsSection));
});
dom.settingsClose.addEventListener("click", closeSettings);
dom.settingsBackdrop.addEventListener("click", closeSettings);
dom.saveSettingsButton.addEventListener("click", saveSettingsAndConnect);
dom.disconnectButton.addEventListener("click", () => {
  closeSettings();
  disconnectSocket("Disconnected from the gateway.", "warning");
});

dom.mobileNavToggle.addEventListener("click", () => {
  if (document.body.classList.contains("sidebar-open")) {
    closeSidebar();
    return;
  }
  openSidebar();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeSettings();
    closeSidebar();
  }
});

render();
resizeComposer();
scrollMessagesToBottom();
connectSocket();
