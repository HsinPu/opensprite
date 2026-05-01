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

const [messageList, runSummaryCard, runTraceViewer, chatComposer, chatPanel, chatClient, copy] = await Promise.all([
  read("src/components/MessageList.vue"),
  read("src/components/RunSummaryCard.vue"),
  read("src/components/RunTraceViewer.vue"),
  read("src/components/ChatComposer.vue"),
  read("src/components/ChatPanel.vue"),
  read("src/composables/useChatClient.js"),
  read("src/i18n/copy.js"),
]);

assertIncludes(messageList, "artifactTypeLabel", "session entry artifact labels");
assertIncludes(messageList, "message__artifact-status", "session entry artifact status");
assertIncludes(runSummaryCard, "visibleDiffPathItems", "diff summary file links");
assertIncludes(runSummaryCard, "cleanup-worktree", "worktree cleanup action");
assertIncludes(runTraceViewer, "codeNavigationResults", "code navigation trace rendering");
assertIncludes(runTraceViewer, "showRetentionSummary", "trace retention summary");
assertIncludes(chatComposer, "composer__commands", "slash command hints rendering");
assertIncludes(chatPanel, "curator-card__scope", "curator scope selector rendering");
assertIncludes(chatClient, "/api/commands", "command catalog fetch");
assertIncludes(chatClient, "/api/curator/status", "curator status fetch");
assertIncludes(chatClient, "/api/curator/", "curator action fetch");
assertIncludes(chatClient, 'params.set("scope", scope)', "curator scoped action fetch");
assertIncludes(chatClient, "CURATOR_POLL_INTERVAL_MS", "curator polling interval");
assertIncludes(chatClient, "scheduleCuratorPoll", "curator polling scheduler");
assertIncludes(chatClient, "curator.completed", "curator event refresh");

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
