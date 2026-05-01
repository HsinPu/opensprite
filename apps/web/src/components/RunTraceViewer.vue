<template>
  <section v-if="run" class="run-trace" :data-collapsed="!expanded" aria-label="Run trace viewer">
    <header class="run-trace__header">
      <div class="run-trace__title">
        <span class="run-trace__eyebrow">{{ copy.trace.title }}</span>
        <strong>{{ run.runId }}</strong>
      </div>
      <div class="run-trace__actions">
        <span class="run-trace__status" :data-status="run.status">{{ run.status }}</span>
        <button class="run-block-toggle" type="button" :aria-expanded="expanded" @click="expanded = !expanded">
          {{ expanded ? copy.trace.collapse : copy.trace.expand }}
        </button>
        <button
          v-if="run.status === 'running'"
          class="run-trace__cancel"
          type="button"
          :disabled="run.cancelPending"
          @click="$emit('cancel-run', run)"
        >
          {{ run.cancelPending ? copy.trace.cancelling : copy.trace.cancelRun }}
        </button>
      </div>
    </header>

    <div v-show="expanded" class="run-trace__body">
      <div class="run-trace__summary" aria-label="Run event summary">
        <span>{{ eventCountLabel }}</span>
        <span v-if="eventCompactionLabel">{{ eventCompactionLabel }}</span>
        <span>{{ artifactCount }} {{ copy.trace.artifacts }}</span>
        <span>{{ parts.length }} {{ copy.trace.parts }}</span>
        <span>{{ toolEventCount }} {{ copy.trace.tool }}</span>
        <span>{{ verificationEventCount }} {{ copy.trace.verification }}</span>
      </div>

      <div class="run-trace__artifacts" aria-label="Run artifacts">
        <div class="run-trace__section-head">
          <strong>{{ copy.trace.artifactHeading }}</strong>
          <span>{{ artifactCount }} {{ copy.trace.artifacts }}</span>
        </div>

        <div v-if="displayedArtifactCount" class="run-trace__artifact-groups">
          <section
            v-for="group in artifactGroups"
            :key="group.kind"
            v-show="group.items.length"
            class="run-trace__artifact-group"
          >
            <div class="run-trace__artifact-group-title">
              <span>{{ group.label }}</span>
              <small>{{ group.items.length }}</small>
            </div>

            <div class="run-trace__artifact-grid">
              <template
                v-for="artifact in group.items"
                :key="artifact.artifactId"
              >
                <details
                  v-if="isToolArtifact(artifact)"
                  class="run-trace__artifact-card run-trace__artifact-card--details"
                  :data-kind="artifact.kind"
                  :data-status="artifact.status"
                >
                  <summary class="run-trace__artifact-summary">
                    <span class="run-trace__artifact-status">{{ artifact.status }}</span>
                    <strong>{{ artifactTitle(artifact) }}</strong>
                    <small v-if="artifactSubtitle(artifact)">{{ artifactSubtitle(artifact) }}</small>
                    <span v-if="artifactDetail(artifact)" class="run-trace__artifact-detail">{{ artifactDetail(artifact) }}</span>
                  </summary>
                  <dl v-if="toolDetailRows(artifact).length" class="run-trace__tool-details">
                    <div v-for="row in toolDetailRows(artifact)" :key="row.label" :data-tone="row.tone || 'neutral'">
                      <dt>{{ row.label }}</dt>
                      <dd>{{ row.value }}</dd>
                    </div>
                  </dl>
                  <p v-else class="run-trace__tool-empty">{{ copy.trace.noToolDetails }}</p>
                </details>

                <button
                  v-else-if="isFileArtifact(artifact)"
                  class="run-trace__artifact-card run-trace__artifact-card--button"
                  :data-kind="'file'"
                  :data-status="artifact.status"
                  type="button"
                  @click="inspectArtifact(artifact)"
                >
                  <span class="run-trace__artifact-status">{{ artifact.status }}</span>
                  <strong>{{ artifactTitle(artifact) }}</strong>
                  <small v-if="artifactSubtitle(artifact)">{{ artifactSubtitle(artifact) }}</small>
                  <span v-if="artifactDetail(artifact)" class="run-trace__artifact-detail">{{ artifactDetail(artifact) }}</span>
                </button>

                <article
                  v-else
                  class="run-trace__artifact-card"
                  :data-kind="artifact.kind"
                  :data-status="artifact.status"
                >
                  <span class="run-trace__artifact-status">{{ artifact.status }}</span>
                  <strong>{{ artifactTitle(artifact) }}</strong>
                  <small v-if="artifactSubtitle(artifact)">{{ artifactSubtitle(artifact) }}</small>
                  <span v-if="artifactDetail(artifact)" class="run-trace__artifact-detail">{{ artifactDetail(artifact) }}</span>
                </article>
              </template>
            </div>
          </section>
        </div>

        <p v-else class="run-trace__empty">{{ copy.trace.noArtifacts }}</p>
      </div>

      <section v-if="codeNavigationResults.length" class="run-trace__code-nav" aria-label="Code navigation results">
        <div class="run-trace__section-head">
          <strong>{{ copy.trace.codeNavigation }}</strong>
          <span>{{ codeNavigationResults.length }} {{ copy.trace.results }}</span>
        </div>

        <div class="run-trace__code-nav-list">
          <article v-for="result in codeNavigationResults" :key="result.id" class="run-trace__code-nav-card">
            <div class="run-trace__code-nav-head">
              <strong>{{ codeNavigationActionLabel(result) }}</strong>
              <span>{{ result.count }} {{ copy.trace.results }}</span>
            </div>
            <div v-if="result.items.length" class="run-trace__code-nav-items">
              <div v-for="item in result.items" :key="`${item.path}:${item.line}:${item.name || item.preview}`" class="run-trace__code-nav-item">
                <code>{{ formatCodeLocation(item) }}</code>
                <strong v-if="item.name">{{ item.name }}</strong>
                <span v-if="item.kind">{{ item.kind }}</span>
                <p v-if="item.preview">{{ item.preview }}</p>
              </div>
            </div>
            <p v-else class="run-trace__empty">{{ copy.trace.noCodeNavigationResults }}</p>
          </article>
        </div>
      </section>

      <section class="run-trace__parts" aria-label="Message parts">
        <div class="run-trace__section-head">
          <button class="run-trace__section-toggle" type="button" :aria-expanded="partsExpanded" @click="partsExpanded = !partsExpanded">
            <strong>{{ copy.trace.messageParts }}</strong>
            <span>{{ parts.length }} {{ copy.trace.parts }}</span>
            <small>{{ partsExpanded ? copy.trace.collapse : copy.trace.expand }}</small>
          </button>
        </div>

        <div v-show="partsExpanded" class="run-trace__section-body">
          <div v-if="visibleParts.length" class="run-trace__part-list">
            <details
              v-for="part in visibleParts"
              :key="part.partId || `${part.partType}:${part.createdAt}`"
              class="run-trace__part"
              :data-kind="part.kind"
              :data-state="part.state"
            >
              <summary>
                <span class="run-trace__part-type">{{ part.partType }}</span>
                <span v-if="part.state" class="run-trace__part-state" :data-state="part.state">{{ partStateLabel(part) }}</span>
                <span v-if="partSummary(part)" class="run-trace__part-summary">{{ partSummary(part) }}</span>
                <time>{{ formatEventTime(part.createdAt) }}</time>
              </summary>
              <div class="run-trace__part-body">
                <pre v-if="part.content">{{ part.content }}</pre>
                <pre v-if="hasMetadata(part)">{{ formatMetadata(part.metadata) }}</pre>
                <p v-if="!part.content && !hasMetadata(part)">{{ copy.trace.noPartContent }}</p>
              </div>
            </details>
          </div>

          <p v-else class="run-trace__empty">{{ copy.trace.noParts }}</p>
        </div>
      </section>

      <section class="run-trace__debug" aria-label="Debug trace events">
        <div class="run-trace__section-head">
          <button class="run-trace__section-toggle" type="button" :aria-expanded="debugExpanded" @click="debugExpanded = !debugExpanded">
            <strong>{{ copy.trace.debugEvents }}</strong>
            <span>{{ events.length }} {{ copy.trace.events }}</span>
            <small>{{ debugExpanded ? copy.trace.collapse : copy.trace.expand }}</small>
          </button>
        </div>

        <div v-show="debugExpanded" class="run-trace__section-body">
          <div class="run-trace__filters" aria-label="Trace event filters">
            <button
              v-for="option in filterOptions"
              :key="option.value"
              type="button"
              :class="{ 'run-trace__filter--active': selectedFilter === option.value }"
              @click="selectedFilter = option.value"
            >
              {{ option.label }}
              <span>{{ option.count }}</span>
            </button>
          </div>

          <div class="run-trace__events">
            <details
              v-for="event in filteredEvents"
              :key="event.id"
              class="run-trace__event"
              :data-category="eventCategory(event)"
            >
              <summary>
                <span class="run-trace__event-type">{{ event.eventType }}</span>
                <span v-if="event.status" class="run-trace__event-status">{{ event.status }}</span>
                <span v-if="eventSummary(event)" class="run-trace__event-summary">{{ eventSummary(event) }}</span>
                <time>{{ formatEventTime(event.createdAt) }}</time>
              </summary>
              <pre>{{ formatPayload(event) }}</pre>
            </details>

            <p v-if="filteredEvents.length === 0" class="run-trace__empty">{{ copy.trace.noEvents }}</p>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from "vue";

import { formatEventTime } from "../composables/useChatClient";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  run: {
    type: Object,
    default: null,
  },
});

const emit = defineEmits(["cancel-run", "inspect-file"]);

const selectedFilter = ref("all");
const expanded = ref(false);
const partsExpanded = ref(false);
const debugExpanded = ref(false);

const events = computed(() => props.run?.rawEvents || props.run?.events || []);
const artifacts = computed(() => props.run?.artifacts || []);
const parts = computed(() => props.run?.parts || []);
const visibleParts = computed(() => parts.value.slice(-8));
const codeNavigationResults = computed(() => parts.value.map(normalizeCodeNavigationResult).filter(Boolean));

const filteredEvents = computed(() => {
  if (selectedFilter.value === "all") {
    return events.value;
  }
  return events.value.filter((event) => eventCategory(event) === selectedFilter.value);
});

const toolEventCount = computed(() => countEventsByCategory("tool"));
const verificationEventCount = computed(() => countEventsByCategory("verification"));
const permissionEventCount = computed(() => countEventsByCategory("permission"));
const textEventCount = computed(() => countEventsByCategory("text"));
const artifactCount = computed(() => artifacts.value.length);
const eventCountLabel = computed(() => {
  const counts = props.run?.eventCounts || {};
  const returned = Number(counts.returned || events.value.length || 0);
  const total = Number(counts.total || returned);
  if (total > returned && typeof props.copy.trace.eventsShown === "function") {
    return props.copy.trace.eventsShown(returned, total);
  }
  return `${events.value.length} ${props.copy.trace.events}`;
});
const eventCompactionLabel = computed(() => {
  const counts = props.run?.eventCounts || {};
  const compacted = Number(counts.compacted || 0);
  if (compacted <= 0 || typeof props.copy.trace.eventsCompacted !== "function") {
    return "";
  }
  return props.copy.trace.eventsCompacted(compacted, Number(counts.textReturned || 0), Number(counts.textTotal || 0));
});

const artifactGroups = computed(() => {
  const toolArtifacts = artifacts.value.filter((artifact) => artifact.kind === "tool");
  const fileArtifacts = artifacts.value.filter((artifact) => artifact.kind === "file" || artifact.path);
  const verificationArtifacts = artifacts.value.filter((artifact) => artifact.kind === "verification");
  const permissionArtifacts = artifacts.value.filter((artifact) => artifact.kind === "permission");
  const taskArtifacts = artifacts.value.filter((artifact) => artifact.kind === "task");
  const workArtifacts = artifacts.value.filter((artifact) => artifact.kind === "work");
  const grouped = new Set([...toolArtifacts, ...fileArtifacts, ...verificationArtifacts, ...permissionArtifacts, ...taskArtifacts, ...workArtifacts]);
  const otherArtifacts = artifacts.value.filter((artifact) => !grouped.has(artifact));

  return [
    {
      kind: "tool",
      label: props.copy.trace.artifactSections.tool,
      items: toolArtifacts,
    },
    {
      kind: "file",
      label: props.copy.trace.artifactSections.file,
      items: fileArtifacts,
    },
    {
      kind: "verification",
      label: props.copy.trace.artifactSections.verification,
      items: verificationArtifacts,
    },
    {
      kind: "permission",
      label: props.copy.trace.artifactSections.permission,
      items: permissionArtifacts,
    },
    {
      kind: "task",
      label: props.copy.trace.artifactSections.task,
      items: taskArtifacts,
    },
    {
      kind: "work",
      label: props.copy.trace.artifactSections.work,
      items: workArtifacts,
    },
    {
      kind: "other",
      label: props.copy.trace.artifactSections.other,
      items: otherArtifacts,
    },
  ];
});

const displayedArtifactCount = computed(() => artifactGroups.value.reduce((total, group) => total + group.items.length, 0));

const filterOptions = computed(() => [
  { value: "all", label: props.copy.trace.filters.all, count: events.value.length },
  { value: "run", label: props.copy.trace.filters.run, count: countEventsByCategory("run") },
  { value: "llm", label: props.copy.trace.filters.llm, count: countEventsByCategory("llm") },
  { value: "tool", label: props.copy.trace.filters.tool, count: toolEventCount.value },
  { value: "verification", label: props.copy.trace.filters.verification, count: verificationEventCount.value },
  { value: "permission", label: props.copy.trace.filters.permission, count: permissionEventCount.value },
  { value: "text", label: props.copy.trace.filters.text, count: textEventCount.value },
  { value: "system", label: props.copy.trace.filters.system, count: countEventsByCategory("system") },
  { value: "work", label: props.copy.trace.filters.work, count: countEventsByCategory("work") },
  { value: "other", label: props.copy.trace.filters.other, count: countEventsByCategory("other") },
]);

function countEventsByCategory(category) {
  return events.value.filter((event) => eventCategory(event) === category).length;
}

function eventCategory(eventType) {
  const event = typeof eventType === "object" ? eventType : null;
  if (["run", "llm", "tool", "verification", "permission", "text", "system", "work"].includes(event?.kind)) {
    return event.kind;
  }
  if (event?.kind) {
    return "other";
  }
  eventType = String(event?.eventType || eventType || "");
  if (eventType.startsWith("run_")) {
    return "run";
  }
  if (eventType.startsWith("llm_")) {
    return "llm";
  }
  if (eventType === "reasoning_delta") {
    return "llm";
  }
  if (eventType.startsWith("tool_")) {
    return "tool";
  }
  if (eventType.startsWith("verification_")) {
    return "verification";
  }
  if (eventType.startsWith("permission_")) {
    return "permission";
  }
  if (eventType === "run_part_delta" || eventType === "message_part_delta") {
    return "text";
  }
  return "other";
}

function eventSummary(event) {
  const artifact = event.artifact || {};
  if (artifact.title || artifact.detail) {
    return [artifact.title, artifact.detail].filter(Boolean).join(" · ");
  }
  const payload = event.payload || {};
  if (payload.tool_name) {
    return [payload.tool_name, payload.input_delta].filter(Boolean).join(" · ");
  }
  if (payload.action) {
    return payload.action;
  }
  if (payload.status) {
    return payload.status;
  }
  if (payload.message) {
    return payload.message;
  }
  if (payload.content_delta) {
    return previewText(payload.content_delta);
  }
  if (payload.error) {
    return payload.error;
  }
  return "";
}

function partSummary(part) {
  const values = [part.toolName, previewText(part.content), part.artifact?.detail].filter(Boolean);
  return values[0] || "";
}

function normalizeCodeNavigationResult(part) {
  if (part?.toolName !== "code_navigation" || part?.partType !== "tool_result") {
    return null;
  }
  let payload = null;
  try {
    payload = JSON.parse(part.content || "{}");
  } catch {
    return null;
  }
  const action = String(payload.action || "").trim();
  const items = Array.isArray(payload.symbols)
    ? payload.symbols
    : Array.isArray(payload.definitions)
      ? payload.definitions
      : Array.isArray(payload.references)
        ? payload.references
        : [];
  const normalizedItems = items.map(normalizeCodeNavigationItem).filter(Boolean).slice(0, 12);
  return {
    id: part.partId || `${action}:${part.createdAt}`,
    action,
    count: items.length,
    items: normalizedItems,
  };
}

function normalizeCodeNavigationItem(item) {
  if (!item || typeof item !== "object") {
    return null;
  }
  const path = String(item.path || "").trim();
  const line = Number(item.line || 0);
  if (!path && !item.name && !item.preview) {
    return null;
  }
  return {
    path,
    line: Number.isFinite(line) && line > 0 ? line : null,
    name: String(item.name || "").trim(),
    kind: String(item.kind || "").trim(),
    preview: String(item.preview || "").trim(),
  };
}

function codeNavigationActionLabel(result) {
  return result.action || props.copy.trace.codeNavigation;
}

function formatCodeLocation(item) {
  if (item.path && item.line) {
    return `${item.path}:${item.line}`;
  }
  return item.path || props.copy.trace.unknownArtifact;
}

function partStateLabel(part) {
  if (part?.metadata?.streaming && part.state === "running") {
    return "streaming";
  }
  return part?.state || "";
}

function previewText(value) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }
  return normalized.length > 96 ? `${normalized.slice(0, 96)}...` : normalized;
}

function hasMetadata(part) {
  return part?.metadata && typeof part.metadata === "object" && Object.keys(part.metadata).length > 0;
}

function formatMetadata(metadata) {
  try {
    return JSON.stringify(metadata || {}, null, 2);
  } catch {
    return String(metadata || "");
  }
}

function artifactTitle(artifact) {
  if (isFileArtifact(artifact)) {
    return artifact.path || props.copy.trace.unknownArtifact;
  }
  return artifact.toolName || artifact.title || artifact.artifactType || props.copy.trace.unknownArtifact;
}

function artifactSubtitle(artifact) {
  if (isFileArtifact(artifact)) {
    return [artifact.action || props.copy.runFileInspector.unknown, artifact.toolName].filter(Boolean).join(" · ");
  }
  if (artifact.kind === "tool") {
    return [artifact.phase, artifact.toolCallId || artifact.iteration].filter(Boolean).join(" · ");
  }
  if (artifact.kind === "verification") {
    return artifact.title && artifact.title !== artifactTitle(artifact) ? artifact.title : "";
  }
  return artifact.artifactType || "";
}

function artifactDetail(artifact) {
  if (isFileArtifact(artifact)) {
    return artifact.diffLen ? props.copy.trace.diffChars(artifact.diffLen) : artifact.detail;
  }
  return artifact.detail;
}

function isToolArtifact(artifact) {
  return artifact.kind === "tool";
}

function isFileArtifact(artifact) {
  return (artifact.kind === "file" || Boolean(artifact.path)) && Boolean(artifact.path);
}

function relatedToolParts(artifact) {
  return parts.value.filter((part) => {
    if (part.kind !== "tool") {
      return false;
    }
    const metadata = part.metadata || {};
    if (artifact.toolCallId && String(metadata.tool_call_id || metadata.toolCallId || part.artifact?.toolCallId || "") === artifact.toolCallId) {
      return true;
    }
    if (artifact.artifactId && part.artifact?.artifactId === artifact.artifactId) {
      return true;
    }
    if (artifact.toolName && part.toolName === artifact.toolName) {
      const partIteration = metadata.iteration ?? part.artifact?.iteration ?? "";
      return !artifact.iteration || String(partIteration) === String(artifact.iteration);
    }
    return false;
  });
}

function toolDetailRows(artifact) {
  const labels = props.copy.trace.detailLabels;
  const sources = [artifact.metadata, ...relatedToolParts(artifact).flatMap((part) => [part.metadata, part.artifact?.metadata])]
    .filter((item) => item && typeof item === "object");
  const rows = [
    { label: labels.toolCallId, value: artifact.toolCallId },
    { label: labels.iteration, value: artifact.iteration },
    { label: labels.phase, value: artifact.phase },
    { label: labels.args, value: firstMetadataValue(sources, ["args_preview", "argsPreview", "arguments", "args"]) },
    { label: labels.result, value: firstMetadataValue(sources, ["result_preview", "resultPreview", "result", "output"]) },
    { label: labels.error, value: firstMetadataValue(sources, ["error", "error_preview", "errorPreview"]), tone: "error" },
    { label: labels.detail, value: artifact.detail },
  ];
  return rows.filter((row) => row.value !== "" && row.value !== null && row.value !== undefined);
}

function firstMetadataValue(sources, keys) {
  for (const source of sources) {
    for (const key of keys) {
      const value = source[key];
      if (value !== "" && value !== null && value !== undefined) {
        return typeof value === "object" ? formatMetadata(value) : String(value);
      }
    }
  }
  return "";
}

function inspectArtifact(artifact) {
  if (!isFileArtifact(artifact)) {
    return;
  }
  emit("inspect-file", {
    changeId: artifact.sourceId || artifact.artifactId,
    path: artifact.path,
    action: artifact.action,
    toolName: artifact.toolName,
    diffLen: artifact.diffLen,
    diff: "",
    diffPreview: artifact.diffPreview || artifact.detail,
    snapshotsAvailable: artifact.snapshotsAvailable,
    artifact,
    createdAt: artifact.createdAt,
  });
}

function formatPayload(event) {
  const payload = event?.payload || event || {};
  const artifact = event?.artifact;
  const value = artifact ? { artifact, payload } : payload;
  try {
    return JSON.stringify(value || {}, null, 2);
  } catch {
    return String(value || "");
  }
}
</script>
