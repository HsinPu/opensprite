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
  curatorSettingsPage,
  settingsModal,
  chatClient,
  dataSettingsActions,
  mcpSettingsActions,
  modelSettingsActions,
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
  read("src/components/CuratorSettingsPage.vue"),
  read("src/components/SettingsModal.vue"),
  read("src/composables/useChatClient.js"),
  read("src/composables/useDataSettingsActions.js"),
  read("src/composables/useMcpSettingsActions.js"),
  read("src/composables/useModelSettingsActions.js"),
  read("src/composables/useNetworkSettingsActions.js"),
  read("src/composables/useProviderSettingsActions.js"),
  read("src/composables/useScheduleSettingsActions.js"),
  read("src/i18n/copy.js"),
  read("styles.css"),
]);

const settingsLogic = `${chatClient}\n${dataSettingsActions}\n${mcpSettingsActions}\n${modelSettingsActions}\n${networkSettingsActions}\n${providerSettingsActions}\n${scheduleSettingsActions}`;

assertIncludes(messageList, "artifactTypeLabel", "session entry artifact labels");
assertIncludes(messageList, "message__artifact-status", "session entry artifact status");
assertIncludes(messageList, "sanitizeVisibleText", "message visible text sanitizer");
assertIncludes(messageList, "normalizeTextPart", "message text-only entry filtering");
assertIncludes(messageList, "system-reminder", "message internal reminder stripping");
assertIncludes(runSummaryCard, "visibleDiffPathItems", "diff summary file links");
assertIncludes(runSummaryCard, "cleanup-worktree", "worktree cleanup action");
assertIncludes(runTraceViewer, "codeNavigationResults", "code navigation trace rendering");
assertIncludes(runTraceViewer, "showRetentionSummary", "trace retention summary");
assertIncludes(styles, ".run-trace__artifact-grid", "trace artifact grid styling");
assertIncludes(styles, "grid-template-columns: 1fr", "trace artifacts render in one column");
assertIncludes(runDetailsPanel, "RunHistorySelector", "run details history selector");
assertIncludes(runDetailsPanel, "showRunHistory", "run history visibility toggle");
assertIncludes(runDetailsPanel, "RunFileChangeDrawer", "run details file drawer");
assertIncludes(chatPanel, "showWorkState && workState", "work state visibility toggle");
assertIncludes(chatPanel, ":show-run-history=\"showRunHistory\"", "run history prop wiring");
assertIncludes(chatComposer, "composer__commands", "slash command hints rendering");
assertIncludes(toastStack, "toast-stack", "toast stack rendering");
assertIncludes(toastStack, "dismiss-toast", "toast dismiss event");
assertIncludes(curatorSettingsPage, "settings-card", "curator settings card layout");
assertIncludes(curatorSettingsPage, "provider-row", "curator settings history layout");
assertIncludes(settingsModal, "CuratorSettingsPage", "curator settings placement");
assertIncludes(settingsModal, "section === 'curator'", "curator settings section");
assertIncludes(settingsModal, "connectedCount", "multiple provider connection count");
assertIncludes(settingsModal, "save-media-model", "media model settings action");
assertIncludes(settingsModal, "textProviderModelGroups", "OpenRouter model grouping");
assertIncludes(settingsModal, "mediaProviderModelGroups", "OpenRouter media model grouping");
assertIncludes(settingsModal, "openRouterModelGroups", "shared OpenRouter model grouping");
assertIncludes(settingsModal, "<optgroup", "grouped model select rendering");
assertIncludes(settingsModal, "@click=\"$emit('select-model', selectedTextProvider.id, settingsState.modelSelections[selectedTextProvider.id])\"", "model selection applies on explicit action");
assertNotIncludes(settingsModal, "@change=\"$emit('select-model', settingsState.selectedTextProviderId", "provider selection does not auto-apply model");
assertIncludes(settingsModal, "settingsState.copilotAuth.userCode", "Copilot auth code rendering");
assertIncludes(settingsModal, "showCodexAuthCard", "conditional Codex auth card");
assertIncludes(settingsModal, "showCopilotAuthCard", "conditional Copilot auth card");
assertIncludes(settingsModal, "form.showWorkState", "work state settings switch");
assertIncludes(settingsModal, "form.showRunHistory", "run history settings switch");
assertIncludes(settingsModal, "hasConnectedProvider", "OAuth auth card connected-provider visibility");
assertIncludes(settingsModal, "providerCredentials", "credential picker rendering");
assertIncludes(settingsModal, "providerEffectiveCredentialId", "effective credential selection");
assertIncludes(settingsModal, "credentialSourceLabel", "credential source status rendering");
assertIncludes(settingsModal, "set-provider-credential", "provider credential selection event");
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
assertIncludes(settingsLogic, "/api/settings/media", "media model settings fetch");
assertIncludes(chatClient, "/api/curator/status", "curator status fetch");
assertIncludes(chatClient, "/api/curator/history", "curator history fetch");
assertIncludes(chatClient, "/api/curator/", "curator action fetch");
assertIncludes(chatClient, 'params.set("scope", scope)', "curator scoped action fetch");
assertIncludes(chatClient, "CURATOR_POLL_INTERVAL_MS", "curator polling interval");
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
]) {
  assertRegex(copy, new RegExp(`${key}\\s*:`), `copy key ${key}`);
}

console.log("web smoke checks passed");
