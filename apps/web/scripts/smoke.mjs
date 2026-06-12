import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

async function read(relativePath) {
  return readFile(join(root, relativePath), "utf8");
}

function assertIncludes(content, needle, label) {
  if (!content.includes(needle)) {
    throw new Error(`${label}: missing ${needle}`);
  }
}

function assertIncludesNormalized(content, needle, label) {
  assertIncludes(content.replace(/\r\n/g, "\n"), needle, label);
}

function assertNotIncludes(content, needle, label) {
  if (content.includes(needle)) {
    throw new Error(`${label}: unexpected ${needle}`);
  }
}

function assertRegex(content, pattern, label) {
  if (!pattern.test(content)) {
    throw new Error(`${label}: expected ${pattern}`);
  }
}

function assertOrder(content, firstNeedle, secondNeedle, label) {
  const firstIndex = content.indexOf(firstNeedle);
  const secondIndex = content.indexOf(secondNeedle);
  if (firstIndex === -1 || secondIndex === -1 || firstIndex > secondIndex) {
    throw new Error(`${label}: expected ${firstNeedle} before ${secondNeedle}`);
  }
}

const [
  messageList,
  messageTextRenderer,
  runSummaryCard,
  runTraceViewer,
  runDetailsPanel,
  chatPanel,
  chatComposer,
  toastStack,
  sidebarNav,
  curatorSettingsPage,
  generalSettingsPage,
  shortcutsSettingsPage,
  app,
  settingsModal,
  chatClient,
  browserSettingsActions,
  mcpSettingsActions,
  modelSettingsActions,
  browserDefaults,
  logDefaults,
  networkDefaults,
  scheduleDefaults,
  searchDefaults,
  runTraceNormalizers,
  settingsNormalizers,
  networkSettingsActions,
  providerSettingsActions,
  scheduleSettingsActions,
  copy,
  styles,
] = await Promise.all([
  read("src/components/MessageList.vue"),
  read("src/components/MessageTextRenderer.vue"),
  read("src/components/RunSummaryCard.vue"),
  read("src/components/RunTraceViewer.vue"),
  read("src/components/RunDetailsPanel.vue"),
  read("src/components/ChatPanel.vue"),
  read("src/components/ChatComposer.vue"),
  read("src/components/ToastStack.vue"),
  read("src/components/SidebarNav.vue"),
  read("src/components/CuratorSettingsPage.vue"),
  read("src/components/GeneralSettingsPage.vue"),
  read("src/components/ShortcutsSettingsPage.vue"),
  read("src/App.vue"),
  read("src/components/SettingsModal.vue"),
  read("src/composables/useChatClient.js"),
  read("src/composables/useBrowserSettingsActions.js"),
  read("src/composables/useMcpSettingsActions.js"),
  read("src/composables/useModelSettingsActions.js"),
  read("src/composables/browserDefaults.js"),
  read("src/composables/logDefaults.js"),
  read("src/composables/networkDefaults.js"),
  read("src/composables/scheduleDefaults.js"),
  read("src/composables/searchDefaults.js"),
  read("src/composables/runTraceNormalizers.js"),
  read("src/composables/settingsNormalizers.js"),
  read("src/composables/useNetworkSettingsActions.js"),
  read("src/composables/useProviderSettingsActions.js"),
  read("src/composables/useScheduleSettingsActions.js"),
  read("src/i18n/copy.js"),
  read("styles.css"),
]);

const settingsLogic = `${chatClient}\n${browserSettingsActions}\n${mcpSettingsActions}\n${modelSettingsActions}\n${browserDefaults}\n${logDefaults}\n${networkDefaults}\n${scheduleDefaults}\n${searchDefaults}\n${settingsNormalizers}\n${networkSettingsActions}\n${providerSettingsActions}\n${scheduleSettingsActions}`;
const settingsUi = `${settingsModal}\n${generalSettingsPage}\n${shortcutsSettingsPage}`;

assertIncludes(messageList, "artifactTypeLabel", "session entry artifact labels");
assertIncludes(messageList, "message__artifact-status", "session entry artifact status");
assertIncludes(messageList, "sanitizeVisibleText", "message visible text sanitizer");
assertIncludes(messageList, "isChatEntry", "run trace entries stay out of the chat transcript");
assertIncludes(messageList, "entryId.startsWith(\"run:\")", "run entry filtering keeps trace artifacts out of chat");
assertIncludes(messageList, "normalizeTextPart", "message text-only entry filtering");
assertIncludes(messageList, "system-reminder", "message internal reminder stripping");
assertIncludes(messageList, "summarizeVisibleToolHistory", "message sanitizer hides raw failed tool history");
assertIncludes(messageList, "--- BEGIN HEAD ---", "message sanitizer recognizes truncated tool context markers");
assertIncludes(messageList, "buildMessageBlocks", "message markdown block normalization");
assertIncludes(messageList, "messageTimeFields", "message timestamp normalization");
assertIncludes(messageList, "class=\"message__time\"", "message timestamp rendering");
assertIncludes(messageList, "type: \"strong\"", "message markdown bold segment normalization");
assertIncludes(messageList, "isMarkdownRule", "message markdown horizontal rule normalization");
assertIncludes(messageTextRenderer, "segment.type === 'strong'", "message markdown bold rendering");
assertIncludes(messageTextRenderer, "message__rule", "message markdown horizontal rule rendering");
assertIncludes(messageTextRenderer, "message__json-card", "message JSON payload collapse rendering");
assertIncludes(messageTextRenderer, "message__code-block", "message code block rendering");
assertIncludes(messageTextRenderer, "message__table", "message table rendering");
assertIncludes(copy, "jsonTitle", "message JSON renderer copy");
assertIncludes(styles, ".message__rendered", "message markdown rendered layout");
assertIncludes(styles, ".message__rule", "message markdown horizontal rule styling");
assertIncludes(styles, ".message__json-card", "message JSON card styling");
assertIncludes(styles, ".message__code-block", "message code block styling");
assertIncludes(runSummaryCard, "visibleDiffPathItems", "diff summary file links");
assertIncludes(runSummaryCard, "cleanup-worktree", "worktree cleanup action");
assertIncludes(runTraceViewer, "codeNavigationResults", "code navigation trace rendering");
assertIncludes(runTraceViewer, "showRetentionSummary", "trace retention summary");
assertIncludes(runTraceViewer, "downloadDebugBundle", "run trace debug export action");
assertIncludes(runTraceViewer, "run-trace__task-dashboard", "task dashboard rendering");
assertIncludes(runTraceViewer, "data-kind", "task dashboard card categories");
assertIncludes(runTraceViewer, "formatOperationAudit", "operation audit trace rendering");
assertIncludes(runTraceViewer, "tool_selection.resolved", "task tool selection summary rendering");
assertNotIncludes(runTraceViewer, "formatApprovalCounts", "task approval lifecycle summary removed");
assertIncludes(runTraceViewer, "formatTaskScorecard", "task scorecard summary rendering");
assertIncludes(runTraceViewer, "decisionTimelineItems", "trace decision timeline rendering");
assertIncludes(runTraceViewer, "deriveDecisionTimelineItems", "trace decision timeline uses shared normalizer");
assertIncludes(runTraceViewer, "run-trace__decision", "trace decision timeline section");
assertIncludes(runTraceNormalizers, "deriveDecisionTimelineItems", "trace decision timeline normalizer export");
assertIncludes(runTraceNormalizers, "task_scorecard.recorded", "trace timeline captures task scorecards");
assertIncludes(runTraceNormalizers, "completion_gate.evaluated", "trace timeline captures completion gate decisions");
assertIncludes(runTraceNormalizers, "task_checkpoint.recorded", "trace timeline captures checkpoint decisions");
assertIncludes(runTraceViewer, "const artifactsExpanded = ref(false)", "run artifacts default collapsed");
assertIncludes(runTraceViewer, "<summary class=\"run-trace__artifact-group-title\">", "run artifact groups are collapsible");
assertNotIncludes(runTraceViewer, "class=\"run-trace__artifact-group\" open", "run artifact groups default collapsed");
assertIncludes(runTraceViewer, "payload.reason", "task context trace reason summary");
assertIncludes(copy, "exportDebug", "run trace debug export copy");
assertIncludes(copy, "taskContextResolved", "task context timeline copy");
assertIncludes(copy, "taskObjectiveResolved", "task objective timeline copy");
assertIncludes(styles, ".run-trace__artifact-grid", "trace artifact grid styling");
assertIncludes(styles, ".run-trace__task-grid", "task dashboard grid styling");
assertIncludes(styles, ".run-trace__decision-list", "decision timeline styling");
assertIncludes(styles, ".run-trace__decision-item", "decision timeline item styling");
assertIncludes(copy, "Decision timeline", "decision timeline copy");
assertIncludesNormalized(styles, ".run-trace__artifacts,\n.run-trace__code-nav", "trace artifacts share boxed section styling");
assertIncludes(styles, "grid-template-columns: 1fr", "trace artifacts render in one column");
assertIncludes(styles, ".run-trace__artifact-group[open] > .run-trace__artifact-group-title::after", "collapsible artifact group right indicator");
assertIncludes(styles, "grid-template-columns: auto minmax(0, 1fr) auto auto", "artifact summary uses right control column");
assertIncludes(styles, "grid-template-columns: auto auto minmax(0, 1fr) auto auto", "trace details use right control column");
assertIncludes(styles, ".run-trace__part[open] > summary::after", "message part right expand indicator");
assertIncludes(styles, ".run-trace__event[open] > summary::after", "debug event right expand indicator");
assertIncludes(runDetailsPanel, "RunHistorySelector", "run details history selector");
assertIncludes(runDetailsPanel, "showRunHistory", "run history visibility toggle");
assertIncludes(runDetailsPanel, "RunFileChangeDrawer", "run details file drawer");
assertIncludes(app, "state.showWorkState && currentWorkState", "work state visibility toggle");
assertIncludes(app, "WorkStateCard", "work state renders in trace sidebar");
assertIncludes(app, "trace-sidebar", "independent right trace sidebar shell");
assertIncludes(app, "traceInspectorCollapsed", "right trace sidebar collapse state");
assertIncludes(app, "toggleTraceInspectorCollapsed", "right trace sidebar collapse action");
assertIncludes(app, "trace-sidebar__resize", "right trace sidebar resize handle");
assertIncludes(app, "opensprite:web:traceInspectorWidth", "right trace sidebar width persistence");
assertIncludes(app, "opensprite:web:sidebarWidth", "left sidebar width persistence");
assertIncludes(app, "beginSidebarResize", "left sidebar resize action");
assertIncludes(app, ":show-run-history=\"state.showRunHistory\"", "run history prop wiring");
assertIncludes(chatComposer, "composer__commands", "slash command hints rendering");
assertIncludes(toastStack, "toast-stack", "toast stack rendering");
assertIncludes(toastStack, "dismiss-toast", "toast dismiss event");
assertIncludes(sidebarNav, "delete-sessions", "sidebar batch session delete event");
assertIncludes(sidebarNav, "deleteMode", "sidebar session delete mode");
assertIncludes(sidebarNav, "session-tile__select", "sidebar session selection checkbox");
assertIncludes(sidebarNav, "brand-mark__expand", "collapsed sidebar brand hover expand affordance");
assertIncludes(sidebarNav, "collapsed && $emit('toggle-sidebar-collapsed')", "collapsed brand mark expands sidebar");
assertIncludes(sidebarNav, "showHiddenSessions", "sidebar hidden session toggle prop");
assertIncludes(sidebarNav, "set-show-hidden-sessions", "sidebar hidden session toggle event");
assertIncludes(sidebarNav, "historySessionLabel", "sidebar non-web sessions show deletable history label");
assertIncludes(sidebarNav, "begin-sidebar-resize", "sidebar resize event");
assertNotIncludes(sidebarNav, "session-tile__delete", "sidebar row delete button removed");
assertIncludes(app, "state.authRequired", "auth gate visibility");
assertIncludes(app, "submitAccessToken", "auth gate submit wiring");
assertIncludes(app, "deleteSessions", "conversation batch delete app wiring");
assertIncludes(app, ":show-hidden-sessions=\"showHiddenSessions\"", "hidden session toggle app prop wiring");
assertIncludes(app, "setShowHiddenSessions", "hidden session toggle app action wiring");
assertIncludes(app, "clearWebSessions", "web conversation clear app wiring");
assertIncludes(app, "confirmDialog", "custom confirm dialog state");
assertIncludes(app, "confirmDialogAction", "custom confirm dialog action");
assertIncludes(app, "deleteSessions: deleteSessionsNow", "conversation delete skips native confirm in client action");
assertIncludes(app, "clearWebSessions: clearWebSessionsNow", "web conversation clear skips native confirm in client action");
assertIncludes(settingsUi, "clear-web-sessions", "settings clear web sessions event");
assertIncludes(settingsModal, "section === 'browser'", "browser settings section");
assertIncludes(settingsModal, "save-browser-settings", "browser settings save event");
assertIncludes(settingsModal, "run-browser-test", "browser settings manual test event");
assertIncludes(settingsModal, "run-browser-doctor", "browser settings doctor event");
assertIncludes(settingsModal, "run-browser-install", "browser settings install event");
assertIncludes(settingsModal, "settingsState.browserForm.launchArgs", "browser launch args field");
assertIncludes(settingsModal, "browserTestSummary", "browser settings manual test summary");
assertIncludes(settingsModal, "browserDoctorSummary", "browser settings doctor summary");
assertIncludes(settingsModal, "browserBackendOptions", "browser backend option rendering");
assertIncludes(copy, "browserbase", "browser cloud backend copy");
assertIncludes(copy, "Show CLI/test history", "hidden session toggle copy");
assertIncludes(copy, "deletable history", "non-web session delete history copy");
assertIncludes(copy, "history sessions", "delete confirmation covers non-web history sessions");
assertIncludes(styles, ".auth-gate", "auth gate styling");
assertIncludes(styles, ".session-history-toggle", "hidden session toggle styling");
assertIncludes(styles, ".sidebar__resize", "left sidebar resize styling");
assertIncludes(styles, "var(--sidebar-width, 268px)", "left sidebar uses resizable width variable");
assertIncludes(chatClient, "STORAGE_KEYS.showHiddenSessions", "hidden session toggle persistence key");
assertIncludes(chatClient, "include_cli", "hidden session toggle fetches CLI/test sessions");
assertIncludes(styles, "var(--trace-sidebar-width, var(--trace-sidebar-default-width))", "desktop resizable trace sidebar width");
assertIncludes(styles, ".app-shell--trace-collapsed", "right trace sidebar collapsed app shell");
assertIncludes(styles, ".trace-sidebar", "right trace sidebar styling");
assertIncludes(styles, ".trace-sidebar__resize", "right trace sidebar resize styling");
assertIncludes(
  styles,
  "min(var(--trace-sidebar-width, 100vw), clamp(360px, 38vw, 480px))",
  "medium width trace sidebar remains inside the viewport",
);
assertIncludesNormalized(
  styles,
  ".trace-sidebar .work-state-card__header,\n  .trace-sidebar .run-summary-card__header,\n  .trace-sidebar .run-timeline__header,\n  .trace-sidebar .run-trace__header {\n    grid-template-columns: 1fr;\n  }",
  "medium width trace card headers do not squeeze titles beside actions",
);
assertIncludes(styles, "scrollbar-gutter: stable", "right trace sidebar keeps an internal scrollbar");
assertIncludes(styles, ".run-trace__part-body pre", "message parts expanded content styling");
assertIncludes(styles, ".run-trace__tool-debug-blocks", "tool cards expose debug payload blocks");
assertIncludes(styles, "overflow-wrap: anywhere", "message parts long text stays in bounds");
assertIncludes(styles, ".message + .message", "chat transcript has separators between messages");
assertIncludes(styles, ".message__time", "chat message timestamp styling");
assertIncludesNormalized(
  styles,
  ".message {\n    grid-template-columns: 28px minmax(0, 1fr);\n    gap: 10px;\n    padding: 16px 0;\n  }\n\n  .message--user {\n    grid-template-columns: minmax(0, 1fr) 28px;\n  }",
  "mobile user messages keep the text column wider than the avatar column",
);
assertIncludes(styles, ".settings-content__header", "settings shared header styling");
assertIncludes(styles, "position: sticky", "settings header remains fixed while scrolling");
assertIncludesNormalized(
  styles,
  ".settings-nav {\n    display: flex;\n    min-height: 0;\n    flex-direction: row;\n    align-items: center;\n    gap: 8px;\n    justify-content: flex-start;\n    overflow-x: auto;\n    overflow-y: hidden;",
  "mobile settings navigation stays compact as horizontal tabs",
);
assertIncludes(styles, ".confirm-dialog", "custom confirmation dialog styling");
assertIncludes(styles, ".session-tile__select", "sidebar session select styling");
assertIncludes(styles, "grid-template-rows: minmax(0, 1fr) auto", "sidebar keeps footer pinned while top content scrolls");
assertIncludes(styles, "grid-template-rows: auto auto auto minmax(0, 1fr)", "sidebar top list can shrink before footer moves");
assertIncludes(styles, "overscroll-behavior: contain", "sidebar session list scroll stays inside the list");
assertIncludes(styles, "scrollbar-gutter: stable", "sidebar session list reserves scrollbar space");
assertIncludes(styles, ".app-shell--sidebar-collapsed .sidebar-collapse-button", "collapsed sidebar hides top restore button");
assertIncludes(styles, ".app-shell--sidebar-collapsed .brand-mark--button:hover .brand-mark__expand", "collapsed sidebar brand hover expand styling");
assertIncludes(styles, ".secondary-button--danger", "settings destructive action styling");
assertIncludes(curatorSettingsPage, "settings-card", "curator settings card layout");
assertIncludes(curatorSettingsPage, "provider-row", "curator settings history layout");
assertIncludes(settingsModal, "CuratorSettingsPage", "curator settings placement");
assertIncludes(settingsModal, "section === 'curator'", "curator settings section");
assertIncludes(settingsModal, "connectedCount", "multiple provider connection count");
assertIncludes(settingsModal, "save-media-model", "media model settings action");
assertIncludes(settingsModal, "textProviderModelGroups", "provider model grouping");
assertIncludes(settingsModal, "mediaProviderModelGroups", "provider media model grouping");
assertIncludes(settingsModal, "slashModelGroups", "shared slash model grouping");
assertIncludes(settingsModal, "model_metadata", "provider model metadata rendering");
assertIncludes(settingsModal, "providerSupportsModelMetadata", "provider profile model metadata rendering");
assertIncludes(settingsModal, "modelMetadata", "provider model metadata copy");
assertNotIncludes(settingsModal, "providerSupportsRequestOptions", "provider request option rendering removed");
assertNotIncludes(settingsModal, "providerRequestOptions", "provider request option state rendering removed");
assertNotIncludes(settingsModal, "apply-provider-recommended-options", "provider request option recommendation event removed");
assertNotIncludes(settingsModal, "provider-options", "provider request option styling hook removed");
assertNotIncludes(modelSettingsActions, "serializeProviderRequestOptions", "provider request option save payload removed");
assertNotIncludes(settingsNormalizers, "providerRequestOptions", "provider request option helpers removed");
assertNotIncludes(settingsLogic, "openRouterOptions", "provider request option state is not provider-specific");
assertIncludes(settingsModal, "textModelOptionLabel", "provider model context option labels");
assertIncludes(settingsModal, "<optgroup", "grouped model select rendering");
assertIncludes(settingsModal, "settingsState.reasoningSelections[selectedTextProvider.id]", "model selection carries reasoning mode");
assertIncludes(settingsModal, "reasoningChoice", "reasoning mode select rendering");
assertNotIncludes(settingsModal, "@change=\"$emit('select-model', settingsState.selectedTextProviderId", "provider selection does not auto-apply model");
assertIncludes(settingsModal, "settingsState.copilotAuth.userCode", "Copilot auth code rendering");
assertIncludes(settingsModal, "showCodexAuthCard", "conditional Codex auth card");
assertIncludes(settingsModal, "showCopilotAuthCard", "conditional Copilot auth card");
assertIncludes(settingsUi, "form.showWorkState", "work state settings switch");
assertIncludes(settingsUi, "form.showRunHistory", "run history settings switch");
assertIncludes(settingsUi, "form.accessToken", "gateway access token setting");
assertNotIncludes(settingsModal, "run-task-completion-smoke", "task completion eval action removed");
assertNotIncludes(settingsModal, "taskCompletionHistoryGroups", "task completion history UI removed");
assertNotIncludes(styles, ".eval-copy-fallback", "task completion debug copy fallback styling removed");
assertIncludes(chatClient, "STORAGE_KEYS.accessToken", "access token preference persistence");
assertIncludes(chatClient, "authorizedHeaders", "authorized API requests");
assertIncludes(chatClient, "access_token", "authorized websocket connection");
assertIncludes(chatClient, "GATEWAY_RECONNECT_DELAY_MS = 30000", "gateway reconnect interval");
assertIncludes(chatClient, "scheduleGatewayReconnect", "gateway reconnect scheduler");
assertIncludes(chatClient, "autoReconnectEnabled", "manual disconnect disables reconnect");
assertIncludes(chatClient, "SESSION_HISTORY_REFRESH_INTERVAL_MS = 30000", "session history refresh interval");
assertIncludes(chatClient, "scheduleSessionHistoryRefresh", "session history auto refresh scheduler");
assertIncludes(chatClient, "loadSessionHistory({ quiet: true })", "session history refresh stays quiet");
assertIncludes(chatClient, "localDraftSessions", "local draft sessions are persisted");
assertIncludes(chatClient, "readStoredDraftSessions", "local draft sessions restore after reload");
assertIncludes(chatClient, "persistLocalDraftSessions", "local draft sessions update with sidebar changes");
assertIncludes(chatClient, "preserveActiveSession: quiet", "quiet history refresh preserves active trace state");
assertIncludes(chatClient, "mergeHistorySession(existingSession, historySession", "history refresh reuses existing session objects");
assertNotIncludes(chatClient, "/api/evals/", "eval API fetches removed from chat client");
assertNotIncludes(chatClient, "deleteTaskCompletionHistoryItem", "task completion history delete fetch removed");
assertNotIncludes(chatClient, "clearTaskCompletionHistory", "task completion history clear fetch removed");
assertIncludes(settingsModal, "hasConnectedProvider", "OAuth auth card connected-provider visibility");
assertIncludes(settingsModal, "providerCredentials", "credential picker rendering");
assertIncludes(settingsModal, "providerEffectiveCredentialId", "effective credential selection");
assertIncludes(settingsModal, "credentialSourceLabel", "credential source status rendering");
assertIncludes(settingsModal, "set-provider-credential", "provider credential selection event");
assertNotIncludes(settingsModal, "decodingModeOptions", "request parameter mode options removed");
assertNotIncludes(settingsLogic, "decoding_mode", "LLM decoding mode save payload removed");
assertNotIncludes(copy, "decodingMode", "request parameter mode copy removed");
assertNotIncludes(settingsModal, "section === 'data'", "data settings section removed");
assertNotIncludes(settingsModal, "settingsState.dataSessions", "data session rendering removed");
assertNotIncludes(settingsModal, "selectedDataSession", "data maintenance dialog state removed");
assertNotIncludes(settingsModal, "copy.settings.data", "data settings copy removed");
assertNotIncludes(settingsModal, "data-timeline-table", "data timeline table rendering removed");
assertNotIncludes(styles, ".data-timeline-table", "data timeline table styling removed");
assertNotIncludes(settingsUi, "props.copy.settings.general.update.branch", "update description hides branch");
assertOrder(settingsModal, "section === 'providers'", "copy.settings.providers.copilotAuth.title", "Copilot auth provider placement");
assertIncludes(chatClient, "/api/commands", "command catalog fetch");
assertIncludes(chatClient, "buildSessionDeletePath", "conversation delete API path");
assertIncludes(chatClient, "buildSessionsClearPath", "web conversation clear API path");
assertIncludes(chatClient, "deletedSessionTombstones", "deleted sessions stay removed during history refresh");
assertIncludes(chatClient, "isDeletedSessionTombstoned", "history merge filters deleted sessions");
assertNotIncludes(chatClient, "copy.value.sidebar.confirmDeleteChat", "conversation delete does not use native confirm in client action");
assertNotIncludes(chatClient, "copy.value.settings.general.clearWebChats.confirm", "web conversation clear does not use native confirm in client action");
assertNotIncludes(chatClient, "copy.value.settings.eval", "eval settings copy removed from chat client");
assertIncludes(settingsLogic, "/api/settings/media", "media model settings fetch");
assertIncludes(settingsLogic, "/api/settings/browser", "browser settings fetch");
assertIncludes(settingsLogic, "/api/settings/browser/test", "browser settings test fetch");
assertIncludes(settingsLogic, "/api/settings/browser/doctor", "browser settings doctor fetch");
assertIncludes(settingsLogic, "/api/settings/browser/install", "browser settings install fetch");
assertNotIncludes(settingsLogic, "/api/settings/permissions", "permission settings fetch removed");
assertNotIncludes(settingsLogic, "/api/settings/tool-access-preview", "tool access preview fetch removed");
assertNotIncludes(settingsLogic, "approval_required_risk_levels", "permission settings approval-risk save payload removed");
assertNotIncludes(settingsLogic, "applyPermissionProfilePreset", "unused permission profile preset helper removed");
assertIncludes(settingsLogic, "launch_args", "browser launch args save payload");
assertIncludes(browserDefaults, "DEFAULT_BROWSER_SESSION_TIMEOUT = 1800", "browser default session timeout");
assertIncludes(browserDefaults, 'DEFAULT_BROWSER_LAUNCH_ARGS = "--no-sandbox"', "browser default launch args");
assertIncludes(browserDefaults, 'DEFAULT_BROWSER_BACKEND = "agent-browser"', "browser default backend");
assertIncludes(browserDefaults, "createDefaultBrowserState", "browser default state factory");
assertIncludes(browserSettingsActions, "normalizeBrowserSettings", "browser actions use shared normalizer");
assertIncludes(settingsLogic, "createDefaultBrowserForm", "settings state uses shared browser form factory");
assertNotIncludes(settingsLogic, "createDefaultPermissionsForm", "settings state permissions form removed");
assertNotIncludes(settingsModal, "section === 'permissions'", "permissions settings section removed");
assertNotIncludes(settingsModal, "save-permissions-settings", "permissions settings save event removed");
assertNotIncludes(settingsModal, "permissionRiskLevelOptions", "permissions risk level option rendering removed");
assertNotIncludes(settingsModal, "toolAccessPreviewRows", "permissions tool access preview rendering removed");
assertNotIncludes(settingsModal, "permissionProfiles", "unused permission profile list removed");
assertNotIncludes(settingsModal, "permissionProfilePresets", "unused permission profile preset rendering removed");
assertNotIncludes(copy, "permissionsLoadFailed", "permission settings load failure copy removed");
assertNotIncludes(styles, ".settings-policy-preview", "tool access preview styling removed");
assertIncludes(chatClient, "/api/curator/status", "curator status fetch");
assertIncludes(chatClient, "/api/curator/history", "curator history fetch");
assertIncludes(chatClient, "/api/curator/", "curator action fetch");
assertIncludes(chatClient, 'params.set("scope", scope)', "curator scoped action fetch");
assertIncludes(chatClient, "CURATOR_POLL_INTERVAL_MS", "curator polling interval");
assertIncludes(chatClient, "task_context.resolved", "task context timeline event");
assertIncludes(chatClient, "task_objective.resolved", "task objective timeline event");
assertIncludes(chatClient, "task_contract.planned", "task contract planned timeline event");
assertIncludes(chatClient, "completion_gate.evaluated", "completion gate timeline event");
assertIncludes(chatClient, "formatTaskObjectiveDetail", "task objective timeline detail");
assertIncludes(chatClient, "formatTaskContractDetail", "task contract timeline detail");
assertIncludes(chatClient, "planner_metadata", "task contract planner metadata detail");
assertIncludes(chatClient, "formatCompletionGateDetail", "completion gate timeline detail");
assertIncludes(chatClient, "missing evidence:", "completion missing evidence detail");
assertIncludes(chatClient, "function previewText", "chat client trace preview helper");
assertIncludes(chatClient, "continuation_type", "task context continuation detail");
assertIncludes(chatClient, "formatTaskContextDetail", "task context timeline detail");
assertIncludes(chatClient, "STORAGE_KEYS.showWorkState", "work state preference persistence");
assertIncludes(chatClient, "STORAGE_KEYS.showRunHistory", "run history preference persistence");
assertIncludes(chatClient, "scheduleCuratorPoll", "curator polling scheduler");
assertIncludes(chatClient, "curator.completed", "curator event refresh");
assertIncludes(chatClient, "viewExternalChatIdForPayload", "external session realtime keying");
assertIncludes(chatClient, "setSettingsSuccess", "settings success toast routing");
assertIncludes(settingsLogic, "connectForm.name", "provider connection naming");
assertIncludes(settingsLogic, "/api/settings/credentials", "credential settings fetch");
assertNotIncludes(settingsLogic, "/api/storage/status", "storage status fetch removed");
assertNotIncludes(settingsLogic, "/api/sessions/timeline", "session timeline fetch removed");
assertNotIncludes(settingsLogic, "loadDataSessionTimeline", "session timeline action export removed");
assertIncludes(settingsLogic, "setProviderCredential", "provider credential switching");
assertIncludes(chatClient, "window.requestAnimationFrame", "message stage deferred scroll");
assertIncludes(chatClient, "currentEntries.value.length, currentMessages.value.length", "message list scroll watch");

for (const key of [
  "artifactTypes",
  "diffSummary",
  "confirmCleanupSandbox",
  "codeNavigationActions",
  "retentionTitle",
  "commandSuggestions",
  "curator",
  "credentialSources",
  "missingCredential",
  "deleteChat",
  "cancelDelete",
  "clearWebChats",
  "confirmDeleteTitle",
  "confirmDeleteChat",
  "confirmDeleteChats",
  "confirmDeleteDetail",
  "confirmDeleteAction",
  "conversationsTitle",
  "sessionDeleted",
  "sessionsDeleted",
  "sessionsDeletedWithFailures",
  "sessionDeleteFailed",
  "sessionsCleared",
]) {
  assertRegex(copy, new RegExp(`${key}\\s*:`), `copy key ${key}`);
}

console.log("web smoke checks passed");
