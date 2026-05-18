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
  runSummaryCard,
  runTraceViewer,
  runDetailsPanel,
  chatPanel,
  chatComposer,
  toastStack,
  sidebarNav,
  curatorSettingsPage,
  app,
  settingsModal,
  chatClient,
  dataSettingsActions,
  browserSettingsActions,
  mcpSettingsActions,
  modelSettingsActions,
  settingsNormalizers,
  networkSettingsActions,
  providerSettingsActions,
  scheduleSettingsActions,
  copy,
  styles,
] = await Promise.all([
  read("src/components/MessageList.vue"),
  read("src/components/RunSummaryCard.vue"),
  read("src/components/RunTraceViewer.vue"),
  read("src/components/RunDetailsPanel.vue"),
  read("src/components/ChatPanel.vue"),
  read("src/components/ChatComposer.vue"),
  read("src/components/ToastStack.vue"),
  read("src/components/SidebarNav.vue"),
  read("src/components/CuratorSettingsPage.vue"),
  read("src/App.vue"),
  read("src/components/SettingsModal.vue"),
  read("src/composables/useChatClient.js"),
  read("src/composables/useDataSettingsActions.js"),
  read("src/composables/useBrowserSettingsActions.js"),
  read("src/composables/useMcpSettingsActions.js"),
  read("src/composables/useModelSettingsActions.js"),
  read("src/composables/settingsNormalizers.js"),
  read("src/composables/useNetworkSettingsActions.js"),
  read("src/composables/useProviderSettingsActions.js"),
  read("src/composables/useScheduleSettingsActions.js"),
  read("src/i18n/copy.js"),
  read("styles.css"),
]);

const settingsLogic = `${chatClient}\n${dataSettingsActions}\n${browserSettingsActions}\n${mcpSettingsActions}\n${modelSettingsActions}\n${settingsNormalizers}\n${networkSettingsActions}\n${providerSettingsActions}\n${scheduleSettingsActions}`;

assertIncludes(messageList, "artifactTypeLabel", "session entry artifact labels");
assertIncludes(messageList, "message__artifact-status", "session entry artifact status");
assertIncludes(messageList, "sanitizeVisibleText", "message visible text sanitizer");
assertIncludes(messageList, "normalizeTextPart", "message text-only entry filtering");
assertIncludes(messageList, "system-reminder", "message internal reminder stripping");
assertIncludes(runSummaryCard, "visibleDiffPathItems", "diff summary file links");
assertIncludes(runSummaryCard, "cleanup-worktree", "worktree cleanup action");
assertIncludes(runTraceViewer, "codeNavigationResults", "code navigation trace rendering");
assertIncludes(runTraceViewer, "showRetentionSummary", "trace retention summary");
assertIncludes(runTraceViewer, "downloadDebugBundle", "run trace debug export action");
assertIncludes(runTraceViewer, "const artifactsExpanded = ref(false)", "run artifacts default collapsed");
assertIncludes(runTraceViewer, "<summary class=\"run-trace__artifact-group-title\">", "run artifact groups are collapsible");
assertNotIncludes(runTraceViewer, "class=\"run-trace__artifact-group\" open", "run artifact groups default collapsed");
assertIncludes(runTraceViewer, "payload.reason", "task context trace reason summary");
assertIncludes(copy, "exportDebug", "run trace debug export copy");
assertIncludes(copy, "taskContextResolved", "task context timeline copy");
assertIncludes(copy, "taskObjectiveResolved", "task objective timeline copy");
assertIncludes(styles, ".run-trace__artifact-grid", "trace artifact grid styling");
assertIncludes(styles, ".run-trace__artifacts,\n.run-trace__code-nav", "trace artifacts share boxed section styling");
assertIncludes(styles, "grid-template-columns: 1fr", "trace artifacts render in one column");
assertIncludes(styles, ".run-trace__artifact-group[open] > .run-trace__artifact-group-title::after", "collapsible artifact group right indicator");
assertIncludes(styles, "grid-template-columns: auto minmax(0, 1fr) auto auto", "artifact summary uses right control column");
assertIncludes(styles, "grid-template-columns: auto auto minmax(0, 1fr) auto auto", "trace details use right control column");
assertIncludes(styles, ".run-trace__part[open] > summary::after", "message part right expand indicator");
assertIncludes(styles, ".run-trace__event[open] > summary::after", "debug event right expand indicator");
assertIncludes(runDetailsPanel, "RunHistorySelector", "run details history selector");
assertIncludes(runDetailsPanel, "showRunHistory", "run history visibility toggle");
assertIncludes(runDetailsPanel, "RunFileChangeDrawer", "run details file drawer");
assertIncludes(chatPanel, "showWorkState && workState", "work state visibility toggle");
assertIncludes(app, "trace-sidebar", "independent right trace sidebar shell");
assertIncludes(app, "traceInspectorCollapsed", "right trace sidebar collapse state");
assertIncludes(app, "toggleTraceInspectorCollapsed", "right trace sidebar collapse action");
assertIncludes(app, "trace-sidebar__resize", "right trace sidebar resize handle");
assertIncludes(app, "opensprite:web:traceInspectorWidth", "right trace sidebar width persistence");
assertIncludes(app, ":show-run-history=\"state.showRunHistory\"", "run history prop wiring");
assertIncludes(chatComposer, "composer__commands", "slash command hints rendering");
assertIncludes(toastStack, "toast-stack", "toast stack rendering");
assertIncludes(toastStack, "dismiss-toast", "toast dismiss event");
assertIncludes(sidebarNav, "delete-sessions", "sidebar batch session delete event");
assertIncludes(sidebarNav, "deleteMode", "sidebar session delete mode");
assertIncludes(sidebarNav, "session-tile__select", "sidebar session selection checkbox");
assertIncludes(sidebarNav, "brand-mark__expand", "collapsed sidebar brand hover expand affordance");
assertIncludes(sidebarNav, "collapsed && $emit('toggle-sidebar-collapsed')", "collapsed brand mark expands sidebar");
assertNotIncludes(sidebarNav, "session-tile__delete", "sidebar row delete button removed");
assertIncludes(app, "state.authRequired", "auth gate visibility");
assertIncludes(app, "submitAccessToken", "auth gate submit wiring");
assertIncludes(app, "deleteSessions", "conversation batch delete app wiring");
assertIncludes(app, "clearWebSessions", "web conversation clear app wiring");
assertIncludes(app, "confirmDialog", "custom confirm dialog state");
assertIncludes(app, "confirmDialogAction", "custom confirm dialog action");
assertIncludes(app, "deleteSessions: deleteSessionsNow", "conversation delete skips native confirm in client action");
assertIncludes(app, "clearWebSessions: clearWebSessionsNow", "web conversation clear skips native confirm in client action");
assertIncludes(app, "clearTaskCompletionHistory: clearTaskCompletionHistoryNow", "eval history clear skips native confirm in client action");
assertIncludes(app, "confirmClearHistoryTitle", "eval history clear custom confirm title");
assertIncludes(settingsModal, "clear-web-sessions", "settings clear web sessions event");
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
assertIncludes(styles, ".auth-gate", "auth gate styling");
assertIncludes(styles, "var(--trace-sidebar-width, var(--trace-sidebar-default-width))", "desktop resizable trace sidebar width");
assertIncludes(styles, ".app-shell--trace-collapsed", "right trace sidebar collapsed app shell");
assertIncludes(styles, ".trace-sidebar", "right trace sidebar styling");
assertIncludes(styles, ".trace-sidebar__resize", "right trace sidebar resize styling");
assertIncludes(styles, "scrollbar-gutter: stable", "right trace sidebar keeps an internal scrollbar");
assertIncludes(styles, ".run-trace__part-body pre", "message parts expanded content styling");
assertIncludes(styles, "overflow-wrap: anywhere", "message parts long text stays in bounds");
assertIncludes(styles, ".settings-content__header", "settings shared header styling");
assertIncludes(styles, "position: sticky", "settings header remains fixed while scrolling");
assertIncludes(styles, ".confirm-dialog", "custom confirmation dialog styling");
assertIncludes(styles, ".session-tile__select", "sidebar session select styling");
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
assertIncludes(settingsModal, "providerSupportsRequestOptions", "provider profile request option rendering");
assertIncludes(settingsModal, "providerRequestOptions", "provider request option state rendering");
assertIncludes(settingsModal, "apply-provider-recommended-options", "provider request option recommendation event");
assertIncludes(settingsModal, "provider-options", "provider request option styling hook");
assertIncludes(modelSettingsActions, "serializeProviderRequestOptions", "provider profile request option save payload");
assertIncludes(settingsNormalizers, "providerRequestOptions", "provider profile request option helpers");
assertNotIncludes(settingsLogic, "openRouterOptions", "provider request option state is not provider-specific");
assertIncludes(settingsModal, "textModelOptionLabel", "provider model context option labels");
assertIncludes(settingsModal, "<optgroup", "grouped model select rendering");
assertIncludes(settingsModal, "semantic_contract_classifier_enabled", "semantic classifier settings switch");
assertIncludes(settingsModal, "semantic_contract_classifier_confidence_threshold", "semantic classifier threshold setting");
assertIncludes(modelSettingsActions, "semantic_contract_classifier_enabled", "semantic classifier settings save payload");
assertIncludes(settingsModal, "@click=\"$emit('select-model', selectedTextProvider.id, settingsState.modelSelections[selectedTextProvider.id])\"", "model selection applies on explicit action");
assertNotIncludes(settingsModal, "@change=\"$emit('select-model', settingsState.selectedTextProviderId", "provider selection does not auto-apply model");
assertIncludes(settingsModal, "settingsState.copilotAuth.userCode", "Copilot auth code rendering");
assertIncludes(settingsModal, "showCodexAuthCard", "conditional Codex auth card");
assertIncludes(settingsModal, "showCopilotAuthCard", "conditional Copilot auth card");
assertIncludes(settingsModal, "form.showWorkState", "work state settings switch");
assertIncludes(settingsModal, "form.showRunHistory", "run history settings switch");
assertIncludes(settingsModal, "form.accessToken", "gateway access token setting");
assertIncludes(settingsModal, "run-task-completion-smoke", "task completion eval action");
assertIncludes(settingsModal, "run-task-completion-live", "live task completion eval action");
assertIncludes(settingsModal, "refresh-task-completion-history", "task completion history refresh action");
assertIncludes(settingsModal, "delete-task-completion-history-item", "task completion history delete action");
assertIncludes(settingsModal, "clear-task-completion-history", "task completion history clear action");
assertIncludes(settingsModal, "failedEvalChecksSummary", "task completion failed-check summary rendering");
assertIncludes(settingsModal, "failedEvalCheckText", "task completion failed-check detail rendering");
assertIncludes(settingsModal, "failedEvalCheckItem", "task completion failed-check item labels");
assertIncludes(settingsModal, "failedEvalCheckHint", "task completion failed-check diagnosis hints");
assertIncludes(settingsModal, "evalResultMeta", "task completion result meta rendering");
assertIncludes(settingsModal, "evalChecksSummary", "task completion result check-count summary");
assertIncludes(settingsModal, "eval-result-row__title", "task completion result status title layout");
assertIncludes(settingsModal, "evalHistoryCaseLabel", "task completion history case labels");
assertIncludes(settingsModal, "evalExpectedSummary", "task completion expected answer display");
assertIncludes(settingsModal, "evalActualResponse", "task completion actual answer display");
assertIncludes(settingsModal, "evalEntryError", "task completion eval error display");
assertIncludes(settingsModal, "copyEvalDebugReport", "task completion debug report copy action");
assertIncludes(settingsModal, "buildEvalDebugReport", "task completion debug report builder");
assertIncludes(settingsModal, "evalCopyButtonLabel", "task completion debug copy button state");
assertIncludes(settingsModal, "resetEvalCopyFallback", "task completion debug fallback reset");
assertIncludes(settingsModal, "isEvalCopySourceEmpty", "task completion stale debug fallback detection");
assertIncludes(settingsModal, "clearTaskCompletionHistory", "task completion history clear resets debug fallback");
assertIncludes(settingsModal, "deleteTaskCompletionHistoryItem", "task completion history delete resets debug fallback");
assertIncludes(settingsModal, "eval-copy-fallback", "task completion debug manual copy fallback");
assertIncludes(settingsModal, "taskCompletionHistoryGroups", "task completion history grouping");
assertIncludes(settingsModal, "toggleEvalHistoryGroup", "task completion history group toggle");
assertIncludes(settingsModal, "evalHistoryBatchId", "task completion history batch grouping");
assertIncludes(settingsModal, "eval-history-group__toggle", "task completion history group accordion");
assertIncludes(settingsModal, "eval-history-row__failures", "task completion history failure details rendering");
assertIncludes(settingsModal, "evalModelLabel", "task completion eval model label rendering");
assertIncludes(styles, ".eval-copy-fallback", "task completion debug copy fallback styling");
assertIncludes(chatClient, "STORAGE_KEYS.accessToken", "access token preference persistence");
assertIncludes(chatClient, "authorizedHeaders", "authorized API requests");
assertIncludes(chatClient, "access_token", "authorized websocket connection");
assertIncludes(chatClient, "/api/evals/task-completion/smoke", "task completion eval fetch");
assertIncludes(chatClient, "/api/evals/task-completion/run", "live task completion eval fetch");
assertIncludes(chatClient, "/api/evals/task-completion/history", "task completion history fetch");
assertIncludes(chatClient, "deleteTaskCompletionHistoryItem", "task completion history delete fetch");
assertIncludes(chatClient, "clearTaskCompletionHistory", "task completion history clear fetch");
assertIncludes(settingsModal, "hasConnectedProvider", "OAuth auth card connected-provider visibility");
assertIncludes(settingsModal, "providerCredentials", "credential picker rendering");
assertIncludes(settingsModal, "providerEffectiveCredentialId", "effective credential selection");
assertIncludes(settingsModal, "credentialSourceLabel", "credential source status rendering");
assertIncludes(settingsModal, "set-provider-credential", "provider credential selection event");
assertIncludes(settingsModal, "decodingModeOptions", "request parameter mode options");
assertIncludes(settingsLogic, "decoding_mode", "LLM decoding mode save payload");
assertIncludes(copy, "Provider default", "request parameter provider default copy");
assertIncludes(settingsModal, "section === 'data'", "data settings section");
assertIncludes(settingsModal, "settingsState.dataSessions", "data session rendering");
assertIncludes(settingsModal, "selectedDataSession", "data maintenance dialog state");
assertIncludes(settingsModal, "copy.settings.data.maintenanceTitle", "data maintenance dialog copy");
assertIncludes(settingsModal, "dataTimelineEntries", "data timeline rendering");
assertIncludes(settingsModal, "data-timeline-table", "data timeline table rendering");
assertIncludes(settingsModal, "toggleTimelineEntry", "data timeline row expansion");
assertIncludes(settingsModal, "timelineItemLabel", "data timeline item labels");
assertIncludes(copy, "timelineColumns", "data timeline table copy");
assertNotIncludes(settingsModal, "props.copy.settings.general.update.branch", "update description hides branch");
assertOrder(settingsModal, "section === 'providers'", "copy.settings.providers.copilotAuth.title", "Copilot auth provider placement");
assertIncludes(chatClient, "/api/commands", "command catalog fetch");
assertIncludes(chatClient, "buildSessionDeletePath", "conversation delete API path");
assertIncludes(chatClient, "buildSessionsClearPath", "web conversation clear API path");
assertNotIncludes(chatClient, "copy.value.sidebar.confirmDeleteChat", "conversation delete does not use native confirm in client action");
assertNotIncludes(chatClient, "copy.value.settings.general.clearWebChats.confirm", "web conversation clear does not use native confirm in client action");
assertNotIncludes(chatClient, "copy.value.settings.eval.confirmClearHistory", "eval history clear does not use native confirm in client action");
assertIncludes(settingsLogic, "/api/settings/media", "media model settings fetch");
assertIncludes(settingsLogic, "/api/settings/browser", "browser settings fetch");
assertIncludes(settingsLogic, "/api/settings/browser/test", "browser settings test fetch");
assertIncludes(settingsLogic, "/api/settings/browser/doctor", "browser settings doctor fetch");
assertIncludes(settingsLogic, "/api/settings/browser/install", "browser settings install fetch");
assertIncludes(settingsLogic, "launch_args", "browser launch args save payload");
assertIncludes(chatClient, "/api/curator/status", "curator status fetch");
assertIncludes(chatClient, "/api/curator/history", "curator history fetch");
assertIncludes(chatClient, "/api/curator/", "curator action fetch");
assertIncludes(chatClient, 'params.set("scope", scope)', "curator scoped action fetch");
assertIncludes(chatClient, "CURATOR_POLL_INTERVAL_MS", "curator polling interval");
assertIncludes(chatClient, "task_context.resolved", "task context timeline event");
assertIncludes(chatClient, "task_objective.resolved", "task objective timeline event");
assertIncludes(chatClient, "task_contract.semantic_classified", "semantic contract timeline event");
assertIncludes(chatClient, "completion_gate.evaluated", "completion gate timeline event");
assertIncludes(chatClient, "formatTaskObjectiveDetail", "task objective timeline detail");
assertIncludes(chatClient, "formatSemanticContractDetail", "semantic contract timeline detail");
assertIncludes(chatClient, "classifier_status", "semantic classifier health detail");
assertIncludes(chatClient, "formatCompletionGateDetail", "completion gate timeline detail");
assertIncludes(chatClient, "needsWebResearch", "web evidence timeline label");
assertIncludes(chatClient, "needsWorkspaceInspection", "workspace evidence timeline label");
assertIncludes(chatClient, "needsHistoryRetrieval", "history evidence timeline label");
assertIncludes(chatClient, "missing evidence:", "completion missing evidence detail");
assertIncludes(copy, "Needs web research", "web evidence trace copy");
assertIncludes(copy, "Needs workspace inspection", "workspace evidence trace copy");
assertIncludes(copy, "Needs history retrieval", "history evidence trace copy");
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
assertIncludes(settingsLogic, "/api/storage/status", "storage status fetch");
assertIncludes(settingsLogic, "/api/sessions/timeline", "session timeline fetch");
assertIncludes(settingsLogic, "loadDataSessionTimeline", "session timeline action export");
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
  "dataLoadFailed",
  "dataTimelineLoadFailed",
  "taskCompletionSmokePassed",
  "taskCompletionEvalSmokeFailed",
  "taskCompletionLivePassed",
  "taskCompletionLiveEvalFailed",
  "taskCompletionHistoryLoadFailed",
  "taskCompletionHistoryDeleteFailed",
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
  "clearHistory",
  "deleteHistoryItem",
  "confirmClearHistoryTitle",
  "confirmClearHistory",
  "confirmClearHistoryDescription",
  "confirmClearHistoryAction",
  "historyCleared",
  "historyGroupTitle",
  "historyGroupMeta",
  "historyBatchLabel",
  "failedChecksTitle",
  "failedChecksForCase",
  "checksSummary",
  "errorLabel",
  "expectedAnswerTitle",
  "actualAnswerTitle",
  "copyDebug",
  "copyAllSmokeDebug",
  "copyAllLiveDebug",
  "copyAllHistoryDebug",
  "copyBatchDebug",
  "copyDebugDescription",
  "debugFallback",
  "debugReportTitle",
  "failedCheckItem",
  "failedCheckItems",
  "failedCheckHints",
  "failedChecks",
  "modelLabel",
]) {
  assertRegex(copy, new RegExp(`${key}\\s*:`), `copy key ${key}`);
}

console.log("web smoke checks passed");
