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

const [messageList, runSummaryCard, runTraceViewer, runDetailsPanel, chatComposer, toastStack, curatorSettingsPage, settingsModal, chatClient, copy] = await Promise.all([
  read("src/components/MessageList.vue"),
  read("src/components/RunSummaryCard.vue"),
  read("src/components/RunTraceViewer.vue"),
  read("src/components/RunDetailsPanel.vue"),
  read("src/components/ChatComposer.vue"),
  read("src/components/ToastStack.vue"),
  read("src/components/CuratorSettingsPage.vue"),
  read("src/components/SettingsModal.vue"),
  read("src/composables/useChatClient.js"),
  read("src/i18n/copy.js"),
]);

assertIncludes(messageList, "artifactTypeLabel", "session entry artifact labels");
assertIncludes(messageList, "message__artifact-status", "session entry artifact status");
assertIncludes(runSummaryCard, "visibleDiffPathItems", "diff summary file links");
assertIncludes(runSummaryCard, "cleanup-worktree", "worktree cleanup action");
assertIncludes(runTraceViewer, "codeNavigationResults", "code navigation trace rendering");
assertIncludes(runTraceViewer, "showRetentionSummary", "trace retention summary");
assertIncludes(runDetailsPanel, "RunHistorySelector", "run details history selector");
assertIncludes(runDetailsPanel, "RunFileChangeDrawer", "run details file drawer");
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
assertIncludes(settingsModal, "<optgroup", "grouped model select rendering");
assertIncludes(settingsModal, "settingsState.copilotAuth.userCode", "Copilot auth code rendering");
assertOrder(settingsModal, "section === 'providers'", "copy.settings.providers.copilotAuth.title", "Copilot auth provider placement");
assertIncludes(chatClient, "/api/commands", "command catalog fetch");
assertIncludes(chatClient, "/api/settings/media", "media model settings fetch");
assertIncludes(chatClient, "/api/curator/status", "curator status fetch");
assertIncludes(chatClient, "/api/curator/history", "curator history fetch");
assertIncludes(chatClient, "/api/curator/", "curator action fetch");
assertIncludes(chatClient, 'params.set("scope", scope)', "curator scoped action fetch");
assertIncludes(chatClient, "CURATOR_POLL_INTERVAL_MS", "curator polling interval");
assertIncludes(chatClient, "scheduleCuratorPoll", "curator polling scheduler");
assertIncludes(chatClient, "curator.completed", "curator event refresh");
assertIncludes(chatClient, "setSettingsSuccess", "settings success toast routing");
assertIncludes(chatClient, "connectForm.name", "provider connection naming");

for (const key of [
  "artifactTypes",
  "diffSummary",
  "confirmCleanupSandbox",
  "codeNavigationActions",
  "retentionTitle",
  "commandSuggestions",
  "curator",
]) {
  assertRegex(copy, new RegExp(`${key}\\s*:`), `copy key ${key}`);
}

console.log("web smoke checks passed");
