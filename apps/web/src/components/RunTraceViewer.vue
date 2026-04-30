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
        <span>{{ events.length }} {{ copy.trace.events }}</span>
        <span>{{ artifactCount }} {{ copy.trace.artifacts }}</span>
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
              <button
                v-for="artifact in group.items"
                :key="artifact.artifactId"
                class="run-trace__artifact-card"
                :class="{ 'run-trace__artifact-card--button': isFileArtifact(artifact) }"
                :data-kind="isFileArtifact(artifact) ? 'file' : artifact.kind"
                :data-status="artifact.status"
                :disabled="!isFileArtifact(artifact)"
                type="button"
                @click="inspectArtifact(artifact)"
              >
                <span class="run-trace__artifact-status">{{ artifact.status }}</span>
                <strong>{{ artifactTitle(artifact) }}</strong>
                <small v-if="artifactSubtitle(artifact)">{{ artifactSubtitle(artifact) }}</small>
                <span v-if="artifactDetail(artifact)" class="run-trace__artifact-detail">{{ artifactDetail(artifact) }}</span>
              </button>
            </div>
          </section>
        </div>

        <p v-else class="run-trace__empty">{{ copy.trace.noArtifacts }}</p>
      </div>

      <section class="run-trace__debug" aria-label="Debug trace events">
        <div class="run-trace__section-head">
          <strong>{{ copy.trace.debugEvents }}</strong>
          <span>{{ events.length }} {{ copy.trace.events }}</span>
        </div>

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

const events = computed(() => props.run?.rawEvents || props.run?.events || []);
const artifacts = computed(() => props.run?.artifacts || []);

const filteredEvents = computed(() => {
  if (selectedFilter.value === "all") {
    return events.value;
  }
  return events.value.filter((event) => eventCategory(event) === selectedFilter.value);
});

const toolEventCount = computed(() => countEventsByCategory("tool"));
const verificationEventCount = computed(() => countEventsByCategory("verification"));
const artifactCount = computed(() => artifacts.value.length);

const artifactGroups = computed(() => {
  const toolArtifacts = artifacts.value.filter((artifact) => artifact.kind === "tool");
  const fileArtifacts = artifacts.value.filter((artifact) => artifact.kind === "file" || artifact.path);
  const verificationArtifacts = artifacts.value.filter((artifact) => artifact.kind === "verification");
  const grouped = new Set([...toolArtifacts, ...fileArtifacts, ...verificationArtifacts]);
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
  { value: "other", label: props.copy.trace.filters.other, count: countEventsByCategory("other") },
]);

function countEventsByCategory(category) {
  return events.value.filter((event) => eventCategory(event) === category).length;
}

function eventCategory(eventType) {
  const event = typeof eventType === "object" ? eventType : null;
  if (event?.kind === "run" || event?.kind === "llm" || event?.kind === "tool" || event?.kind === "verification") {
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
  if (eventType.startsWith("tool_")) {
    return "tool";
  }
  if (eventType.startsWith("verification_")) {
    return "verification";
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
    return payload.tool_name;
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
  if (payload.error) {
    return payload.error;
  }
  return "";
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

function isFileArtifact(artifact) {
  return (artifact.kind === "file" || Boolean(artifact.path)) && Boolean(artifact.path);
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
