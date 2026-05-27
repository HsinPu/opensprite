<template>
  <section v-if="run" class="run-trace" :data-collapsed="!expanded" aria-label="Run trace viewer">
    <header class="run-trace__header">
      <div class="run-trace__title">
        <span class="run-trace__eyebrow">{{ copy.trace.title }}</span>
        <strong>{{ run.runId }}</strong>
        <span class="run-trace__status" :data-status="run.status">{{ run.status }}</span>
      </div>
      <div class="run-trace__actions">
        <button class="run-block-toggle" type="button" :aria-expanded="expanded" @click="expanded = !expanded">
          {{ expanded ? copy.trace.collapse : copy.trace.expand }}
        </button>
        <button class="run-block-toggle" type="button" @click="downloadDebugBundle">
          {{ copy.trace.exportDebug }}
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
        <span>{{ processEventCount }} {{ copy.trace.filters.process }}</span>
        <span>{{ verificationEventCount }} {{ copy.trace.verification }}</span>
      </div>

      <div v-if="showRetentionSummary" class="run-trace__retention" aria-label="Trace retention summary">
        <strong>{{ copy.trace.retentionTitle }}</strong>
        <span>{{ copy.trace.retentionEvents(retentionCounts.returned, retentionCounts.total) }}</span>
        <span>{{ copy.trace.retentionCompacted(retentionCounts.compacted) }}</span>
        <span v-if="retentionCounts.textTotal > 0">{{ copy.trace.retentionText(retentionCounts.textReturned, retentionCounts.textTotal) }}</span>
      </div>

      <div v-if="harnessSummaryRows.length" class="run-trace__harness-dashboard" aria-label="Harness dashboard">
        <div class="run-trace__harness-head">
          <strong>{{ copy.trace.harnessTitle }}</strong>
          <small>{{ copy.trace.harnessSubtitle }}</small>
        </div>
        <div class="run-trace__harness-grid">
          <article v-for="row in harnessSummaryRows" :key="row.label" class="run-trace__harness-card" :data-kind="row.kind">
            <small>{{ row.label }}</small>
            <strong>{{ row.value }}</strong>
          </article>
        </div>
      </div>

      <section v-if="decisionTimelineItems.length" class="run-trace__decision" aria-label="Decision timeline">
        <div class="run-trace__section-head">
          <strong>{{ copy.trace.decisionTimeline.title }}</strong>
          <span>{{ copy.trace.decisionTimeline.count(decisionTimelineItems.length) }}</span>
        </div>

        <div class="run-trace__decision-list">
          <details
            v-for="item in decisionTimelineItems"
            :key="item.id"
            class="run-trace__decision-item"
            :data-phase="item.phase"
            :data-status="item.status"
          >
            <summary>
              <span class="run-trace__decision-marker" aria-hidden="true"></span>
              <div class="run-trace__decision-main">
                <div class="run-trace__decision-title">
                  <strong>{{ decisionTimelineTitle(item) }}</strong>
                  <span>{{ decisionTimelinePhaseLabel(item) }}</span>
                </div>
                <p v-if="item.summary">{{ item.summary }}</p>
              </div>
              <time>{{ formatEventTime(item.createdAt) }}</time>
            </summary>

            <dl v-if="item.details.length" class="run-trace__decision-details">
              <div v-for="detail in item.details" :key="`${item.id}:${detail.labelKey}`" :data-tone="detail.tone || 'neutral'">
                <dt>{{ decisionTimelineDetailLabel(detail) }}</dt>
                <dd>{{ detail.value }}</dd>
              </div>
            </dl>
          </details>
        </div>
      </section>

      <div class="run-trace__artifacts" aria-label="Run artifacts">
        <div class="run-trace__section-head">
          <button class="run-trace__section-toggle" type="button" :aria-expanded="artifactsExpanded" @click="artifactsExpanded = !artifactsExpanded">
            <strong>{{ copy.trace.artifactHeading }}</strong>
            <span>{{ artifactCount }} {{ copy.trace.artifacts }}</span>
            <small>{{ artifactsExpanded ? copy.trace.collapse : copy.trace.expand }}</small>
          </button>
        </div>

        <div v-show="artifactsExpanded" class="run-trace__section-body">
          <details v-if="parallelDelegationGroups.length" class="run-trace__artifact-group">
            <summary class="run-trace__artifact-group-title">
              <span>{{ copy.trace.parallelDelegation }}</span>
              <small>{{ parallelDelegationGroups.length }}</small>
            </summary>

            <div class="run-trace__parallel-groups">
              <article
                v-for="group in parallelDelegationGroups"
                :key="group.groupId"
                class="run-trace__parallel-group"
                :data-status="group.status"
              >
                <div class="run-trace__parallel-group-head">
                  <div>
                    <strong>{{ copy.trace.parallelGroup(group.shortId) }}</strong>
                    <small>{{ parallelGroupCountLabel(group) }}</small>
                  </div>
                  <span class="run-trace__artifact-status">{{ parallelStatusLabel(group.status) }}</span>
                </div>

                <div v-if="parallelGroupStatusEntries(group).length" class="run-trace__parallel-group-chips">
                  <code
                    v-for="entry in parallelGroupStatusEntries(group)"
                    :key="entry.status"
                    :data-status="entry.status"
                  >
                    {{ entry.label }} x{{ entry.count }}
                  </code>
                </div>

                <div class="run-trace__parallel-task-list">
                  <article
                    v-for="artifact in group.items"
                    :key="artifact.artifactId"
                    class="run-trace__parallel-task"
                    :data-status="artifact.status"
                  >
                    <span class="run-trace__artifact-status">{{ parallelStatusLabel(artifact.status) }}</span>
                    <strong>{{ parallelTaskTitle(artifact) }}</strong>
                    <small v-if="parallelTaskMeta(artifact)">{{ parallelTaskMeta(artifact) }}</small>
                    <span v-if="artifactDetail(artifact)" class="run-trace__artifact-detail">{{ artifactDetail(artifact) }}</span>
                  </article>
                </div>
              </article>
            </div>
          </details>

          <div v-if="displayedArtifactCount" class="run-trace__artifact-groups">
            <details
              v-for="group in artifactGroups"
              :key="group.kind"
              v-show="group.items.length"
              class="run-trace__artifact-group"
            >
              <summary class="run-trace__artifact-group-title">
                <span>{{ group.label }}</span>
                <small>{{ group.items.length }}</small>
              </summary>

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
                    <div v-if="toolDebugBlocks(artifact).length" class="run-trace__tool-debug-blocks">
                      <details
                        v-for="block in toolDebugBlocks(artifact)"
                        :key="block.key"
                        class="run-trace__debug-block"
                        :open="block.open"
                      >
                        <summary>
                          <strong>{{ block.title }}</strong>
                          <span v-if="block.meta">{{ block.meta }}</span>
                        </summary>
                        <pre>{{ block.content }}</pre>
                      </details>
                    </div>
                    <p v-if="!toolDetailRows(artifact).length && !toolDebugBlocks(artifact).length" class="run-trace__tool-empty">{{ copy.trace.noToolDetails }}</p>
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

                  <details
                    v-else-if="isStructuredSubagentArtifact(artifact)"
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
                    <dl v-if="subagentDetailRows(artifact).length" class="run-trace__tool-details">
                      <div v-for="row in subagentDetailRows(artifact)" :key="row.label" :data-tone="row.tone || 'neutral'">
                        <dt>{{ row.label }}</dt>
                        <dd>{{ row.value }}</dd>
                      </div>
                    </dl>
                    <pre v-if="structuredSubagentPreview(artifact)" class="run-trace__structured-preview">{{ structuredSubagentPreview(artifact) }}</pre>
                    <p v-else class="run-trace__tool-empty">{{ copy.trace.structuredOutputEmpty }}</p>
                  </details>

                  <details
                    v-else-if="isProcessArtifact(artifact)"
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
                    <dl v-if="processDetailRows(artifact).length" class="run-trace__tool-details">
                      <div v-for="row in processDetailRows(artifact)" :key="row.label" :data-tone="row.tone || 'neutral'">
                        <dt>{{ row.label }}</dt>
                        <dd>{{ row.value }}</dd>
                      </div>
                    </dl>
                    <p v-else class="run-trace__tool-empty">{{ copy.trace.noToolDetails }}</p>
                  </details>

                  <details
                    v-else-if="isTaskArtifactSummary(artifact)"
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
                    <dl v-if="taskArtifactRows(artifact).length" class="run-trace__tool-details">
                      <div v-for="row in taskArtifactRows(artifact)" :key="row.label" :data-tone="row.tone || 'neutral'">
                        <dt>{{ row.label }}</dt>
                        <dd>{{ row.value }}</dd>
                      </div>
                    </dl>
                    <div v-if="taskArtifactSources(artifact).length" class="run-trace__source-list">
                      <article v-for="source in taskArtifactSources(artifact)" :key="`${source.url}:${source.title}`" class="run-trace__source-card">
                        <a v-if="source.url" :href="source.url" target="_blank" rel="noreferrer">{{ source.title || source.domain || source.url }}</a>
                        <strong v-else>{{ source.title || copy.trace.unknownArtifact }}</strong>
                        <small v-if="source.url">{{ source.url }}</small>
                        <p v-if="source.snippet">{{ source.snippet }}</p>
                        <span v-if="source.meta || source.backend">{{ [source.meta, source.backend].filter(Boolean).join(" / ") }}</span>
                      </article>
                    </div>
                    <p v-else class="run-trace__tool-empty">{{ copy.trace.noTaskArtifactSources }}</p>
                  </details>

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
            </details>
          </div>

          <p v-else class="run-trace__empty">{{ copy.trace.noArtifacts }}</p>
        </div>
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
            <p v-if="result.error" class="run-trace__code-nav-error">{{ result.error }}</p>
            <div v-if="result.items.length" class="run-trace__code-nav-items">
              <div v-for="item in result.items" :key="`${item.path}:${item.line}:${item.name || item.preview}`" class="run-trace__code-nav-item">
                <code>{{ formatCodeLocation(item) }}</code>
                <strong v-if="item.name">{{ item.name }}</strong>
                <span v-if="item.kind">{{ item.kind }}</span>
                <p v-if="item.preview">{{ item.preview }}</p>
              </div>
              <small v-if="result.hiddenCount > 0" class="run-trace__code-nav-more">{{ copy.trace.moreResults(result.hiddenCount) }}</small>
            </div>
            <p v-else-if="!result.error" class="run-trace__empty">{{ copy.trace.noCodeNavigationResults }}</p>
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
import { deriveDecisionTimelineItems } from "../composables/runTraceNormalizers";

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
const artifactsExpanded = ref(false);
const partsExpanded = ref(false);
const debugExpanded = ref(false);

const events = computed(() => props.run?.rawEvents || props.run?.events || []);
const artifacts = computed(() => props.run?.artifacts || []);
const parts = computed(() => props.run?.parts || []);
const visibleParts = computed(() => parts.value.slice(-8));
const codeNavigationResults = computed(() => parts.value.map(normalizeCodeNavigationResult).filter(Boolean));
const decisionTimelineItems = computed(() => deriveDecisionTimelineItems(events.value));

const filteredEvents = computed(() => {
  if (selectedFilter.value === "all") {
    return events.value;
  }
  return events.value.filter((event) => eventCategory(event) === selectedFilter.value);
});

const toolEventCount = computed(() => countEventsByCategory("tool"));
const processEventCount = computed(() => countEventsByCategory("process"));
const verificationEventCount = computed(() => countEventsByCategory("verification"));
const permissionEventCount = computed(() => countEventsByCategory("permission"));
const textEventCount = computed(() => countEventsByCategory("text"));
const harnessEventCount = computed(() => countEventsByCategory("harness"));
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

const retentionCounts = computed(() => {
  const counts = props.run?.eventCounts || {};
  const returned = Number(counts.returned || events.value.length || 0);
  const total = Number(counts.total || returned);
  return {
    returned,
    total,
    compacted: Number(counts.compacted || Math.max(0, total - returned)),
    textReturned: Number(counts.textReturned || 0),
    textTotal: Number(counts.textTotal || 0),
  };
});

const showRetentionSummary = computed(() => retentionCounts.value.compacted > 0 || retentionCounts.value.textTotal > retentionCounts.value.textReturned);

const harnessSummaryRows = computed(() => {
  const labels = props.copy.trace.harnessLabels || {};
  const initialProfilePayload = latestEventPayload("harness_profile.initial_selected");
  const legacyProfilePayload = latestEventPayload("harness_profile.selected");
  const effectiveProfilePayload = latestEventPayload("harness_profile.effective_selected");
  const changedProfilePayload = latestEventPayload("harness_profile.changed");
  const profilePayload = Object.keys(effectiveProfilePayload).length
    ? effectiveProfilePayload
    : (Object.keys(legacyProfilePayload).length ? legacyProfilePayload : initialProfilePayload);
  const policyPayload = latestEventPayload("harness_policy.selected");
  const eventCheckpointPayload = latestEventPayload("harness_checkpoint.recorded");
  const partCheckpointPayload = latestPartMetadata("harness_checkpoint");
  const operationAuditPayload = latestPartMetadata("operation_audit");
  const policyResolutionPayload = latestEventPayload("harness_policy.merge_resolved");
  const evalPayload = latestEventPayload("harness_eval.completed");
  const failedEvalPayload = latestEventPayload("harness_eval.failed");
  const evalPartPayload = latestPartMetadata("harness_eval_result");
  const evalSource = Object.keys(evalPayload).length ? evalPayload : (Object.keys(failedEvalPayload).length ? failedEvalPayload : evalPartPayload);
  const scorecardEventPayload = latestEventPayload("harness_scorecard.recorded");
  const scorecardPartPayload = latestPartMetadata("harness_scorecard");
  const scorecardPayload = Object.keys(scorecardEventPayload).length ? scorecardEventPayload : scorecardPartPayload;
  const hasEventCheckpoint = Object.keys(eventCheckpointPayload).length > 0;
  const checkpointPayload = hasEventCheckpoint ? eventCheckpointPayload : partCheckpointPayload;
  const checkpointSource = hasEventCheckpoint ? "event" : (Object.keys(partCheckpointPayload).length ? "part" : "");
  const contractPayload = latestEventPayload("task_contract.created");
  const completionPayload = latestEventPayload("completion_gate.evaluated");
  const autoContinueEvent = latestEventWithPrefix("auto_continue.");
  const checkpointContract = checkpointPayload.task_contract || checkpointPayload.taskContract || {};
  const checkpointCompletion = checkpointPayload.completion || {};
  const checkpointProgress = checkpointPayload.work_progress || checkpointPayload.workProgress || {};
  const contractSource = Object.keys(checkpointContract).length ? checkpointContract : contractPayload;
  const contractProfile = contractSource.harness_profile || contractSource.harnessProfile || checkpointPayload.harness_profile || checkpointPayload.harnessProfile || {};
  const policySource = Object.keys(policyPayload).length ? policyPayload : (checkpointPayload.harness_policy || checkpointPayload.harnessPolicy || {});
  const completionSource = Object.keys(checkpointCompletion).length ? checkpointCompletion : completionPayload;
  const profileName = profilePayload.name || contractProfile.name || "";
  const taskType = contractSource.task_type || contractSource.taskType || profilePayload.task_type || profilePayload.taskType || "";
  const profileSelection = profilePayload.selection || contractProfile.selection || {};
  if (!profileName && !taskType && !Object.keys(contractSource).length && !Object.keys(policySource).length && !Object.keys(checkpointPayload).length && !Object.keys(policyResolutionPayload).length && !Object.keys(evalSource).length && !Object.keys(scorecardPayload).length && !Object.keys(changedProfilePayload).length) {
    return [];
  }
  const toolPermissionCounts = countToolPermissionDecisions();
  const approvalCounts = countToolApprovalEvents();
  const evalSummary = evalSource.summary || {};
  const rows = [
    { label: labels.profile || "Profile", value: profileName, kind: "profile" },
    { label: labels.initialProfile || "Initial profile", value: formatHarnessProfilePayload(initialProfilePayload), kind: "profile" },
    { label: labels.effectiveProfile || "Effective profile", value: formatHarnessProfilePayload(effectiveProfilePayload), kind: "profile" },
    { label: labels.profileChange || "Profile change", value: formatHarnessProfileChange(changedProfilePayload), kind: "profile" },
    { label: labels.taskType || "Task", value: taskType, kind: "profile" },
    { label: labels.selection || "Selection", value: formatProfileSelection(profileSelection), kind: "profile" },
    { label: labels.policy || "Policy", value: policySource.name, kind: "policy" },
    { label: labels.verification || "Verification", value: profilePayload.verification_policy || contractProfile.verification_policy || contractProfile.verificationPolicy, kind: "contract" },
    { label: labels.continuation || "Continuation", value: profilePayload.continuation_policy || contractProfile.continuation_policy || contractProfile.continuationPolicy, kind: "contract" },
    { label: labels.evidence || "Evidence", value: countPayloadItems(contractSource.requirements), kind: "contract" },
    { label: labels.criteria || "Criteria", value: countPayloadItems(contractSource.acceptance_criteria || contractSource.acceptanceCriteria), kind: "contract" },
    { label: labels.missingEvidence || "Missing", value: formatMissingEvidence(completionSource.missing_evidence || completionSource.missingEvidence), kind: "completion" },
    { label: labels.policyRisks || "Risk", value: formatPolicyRisks(policySource, labels), kind: "policy" },
    { label: labels.policyResolution || "Policy resolution", value: formatPolicyResolution(policyResolutionPayload, labels), kind: "policy" },
    { label: labels.toolDecisions || "Tool decisions", value: formatToolDecisionCounts(toolPermissionCounts, labels), kind: "policy" },
    { label: labels.approvals || "Approvals", value: formatApprovalCounts(approvalCounts, labels), kind: "policy" },
    { label: labels.completion || "Completion", value: compactJoin([completionSource.status, completionSource.reason], " · "), kind: "completion" },
    { label: labels.nextAction || "Next", value: checkpointPayload.next_action || checkpointPayload.nextAction || checkpointProgress.next_action || checkpointProgress.nextAction, kind: "next" },
    { label: labels.artifacts || "Artifacts", value: checkpointPayload.task_artifact_count ?? checkpointPayload.taskArtifactCount, kind: "evidence" },
    { label: labels.checkpoint || "Checkpoint", value: formatCheckpoint(checkpointPayload, checkpointSource, labels), kind: "checkpoint" },
    { label: labels.autoContinue || "Auto", value: autoContinueEvent ? compactJoin([autoContinueEvent.eventType.replace("auto_continue.", ""), autoContinueEvent.payload?.reason], " · ") : "", kind: "next" },
    { label: labels.operationAudit || "Audit", value: formatOperationAudit(operationAuditPayload), kind: "checkpoint" },
    { label: labels.evalResults || "Eval", value: formatHarnessEvalResult(evalSource, evalSummary, labels), kind: "checkpoint" },
    { label: labels.scorecard || "Scorecard", value: formatHarnessScorecard(scorecardPayload, labels), kind: "checkpoint" },
  ];
  return rows.filter((row) => row.value !== "" && row.value !== null && row.value !== undefined);
});

const parallelDelegationGroups = computed(() => {
  const groups = new Map();
  for (const artifact of artifacts.value) {
    const groupId = parallelGroupId(artifact);
    if (!groupId) {
      continue;
    }
    const current = groups.get(groupId) || {
      groupId,
      shortId: shortenParallelGroupId(groupId),
      total: 0,
      items: [],
      createdAt: artifact.createdAt,
    };
    current.items.push(artifact);
    current.total = Math.max(current.total, parallelGroupTotal(artifact) || 0, current.items.length);
    current.createdAt = Math.min(current.createdAt, artifact.createdAt || current.createdAt);
    groups.set(groupId, current);
  }
  return Array.from(groups.values())
    .map((group) => {
      group.items.sort((left, right) => {
        const leftIndex = parallelGroupIndex(left);
        const rightIndex = parallelGroupIndex(right);
        if (leftIndex !== rightIndex) {
          return leftIndex - rightIndex;
        }
        return (left.createdAt || 0) - (right.createdAt || 0);
      });
      group.total = Math.max(group.total, group.items.length);
      group.status = summarizeParallelGroupStatus(group.items);
      group.statusCounts = countParallelStatuses(group.items.map((item) => item.status));
      return group;
    })
    .sort((left, right) => left.createdAt - right.createdAt);
});

const groupedParallelArtifactIds = computed(() => new Set(
  parallelDelegationGroups.value.flatMap((group) => group.items.map((item) => item.artifactId)),
));

const artifactGroups = computed(() => {
  const toolArtifacts = artifacts.value.filter((artifact) => artifact.kind === "tool");
  const fileArtifacts = artifacts.value.filter((artifact) => artifact.kind === "file" || artifact.path);
  const verificationArtifacts = artifacts.value.filter((artifact) => artifact.kind === "verification");
  const permissionArtifacts = artifacts.value.filter((artifact) => artifact.kind === "permission");
  const taskArtifacts = artifacts.value.filter((artifact) => artifact.kind === "task");
  const processArtifacts = artifacts.value.filter((artifact) => artifact.kind === "process");
  const workArtifacts = artifacts.value.filter((artifact) => artifact.kind === "work" && !groupedParallelArtifactIds.value.has(artifact.artifactId));
  const groupedParallelArtifacts = artifacts.value.filter((artifact) => groupedParallelArtifactIds.value.has(artifact.artifactId));
  const grouped = new Set([
    ...toolArtifacts,
    ...fileArtifacts,
    ...verificationArtifacts,
    ...permissionArtifacts,
    ...taskArtifacts,
    ...processArtifacts,
    ...workArtifacts,
    ...groupedParallelArtifacts,
  ]);
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
      kind: "process",
      label: props.copy.trace.artifactSections.process,
      items: processArtifacts,
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

const displayedArtifactCount = computed(() => {
  const groupedCount = parallelDelegationGroups.value.reduce((total, group) => total + group.items.length, 0);
  return groupedCount + artifactGroups.value.reduce((total, group) => total + group.items.length, 0);
});

const filterOptions = computed(() => [
  { value: "all", label: props.copy.trace.filters.all, count: events.value.length },
  { value: "run", label: props.copy.trace.filters.run, count: countEventsByCategory("run") },
  { value: "llm", label: props.copy.trace.filters.llm, count: countEventsByCategory("llm") },
  { value: "tool", label: props.copy.trace.filters.tool, count: toolEventCount.value },
  { value: "verification", label: props.copy.trace.filters.verification, count: verificationEventCount.value },
  { value: "permission", label: props.copy.trace.filters.permission, count: permissionEventCount.value },
  { value: "harness", label: props.copy.trace.filters.harness, count: harnessEventCount.value },
  { value: "process", label: props.copy.trace.filters.process, count: processEventCount.value },
  { value: "text", label: props.copy.trace.filters.text, count: textEventCount.value },
  { value: "system", label: props.copy.trace.filters.system, count: countEventsByCategory("system") },
  { value: "work", label: props.copy.trace.filters.work, count: countEventsByCategory("work") },
  { value: "other", label: props.copy.trace.filters.other, count: countEventsByCategory("other") },
]);

function countEventsByCategory(category) {
  return events.value.filter((event) => eventCategory(event) === category).length;
}

function countToolPermissionDecisions() {
  const counts = { checked: 0, allowed: 0, denied: 0, approvalRequired: 0 };
  for (const event of events.value) {
    const eventType = String(event?.eventType || "");
    if (!eventType.startsWith("tool_permission.")) {
      continue;
    }
    if (eventType.endsWith(".checked")) counts.checked += 1;
    if (eventType.endsWith(".allowed")) counts.allowed += 1;
    if (eventType.endsWith(".denied")) counts.denied += 1;
    if (eventType.endsWith(".approval_required")) counts.approvalRequired += 1;
  }
  return counts;
}

function countToolApprovalEvents() {
  const counts = { requested: 0, approved: 0, denied: 0, expired: 0 };
  for (const event of events.value) {
    const eventType = String(event?.eventType || "");
    if (eventType === "tool_approval.requested") counts.requested += 1;
    if (eventType === "tool_approval.approved") counts.approved += 1;
    if (eventType === "tool_approval.denied") counts.denied += 1;
    if (eventType === "tool_approval.expired") counts.expired += 1;
  }
  return counts;
}

function eventCategory(eventType) {
  const event = typeof eventType === "object" ? eventType : null;
  if (["run", "llm", "tool", "verification", "permission", "process", "text", "system", "work", "harness"].includes(event?.kind)) {
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
  if (eventType.startsWith("harness_") || eventType.startsWith("task_contract.")) {
    return "harness";
  }
  if (eventType === "run_part_delta" || eventType === "message_part_delta") {
    return "text";
  }
  if (eventType.startsWith("background_process.")) {
    return "process";
  }
  return "other";
}

function formatPolicyResolution(payload, labels) {
  if (!payload || !Object.keys(payload).length) {
    return "";
  }
  return compactJoin([
    `${countPayloadItems(payload.constraints_applied || payload.constraintsApplied)} ${labels.constraints || "constraints"}`,
    `${countPayloadItems(payload.blocked_relaxations || payload.blockedRelaxations)} ${labels.blockedRelaxations || "blocked relaxations"}`,
  ], " · ");
}

function formatToolDecisionCounts(counts, labels) {
  if (!counts.checked && !counts.allowed && !counts.denied && !counts.approvalRequired) {
    return "";
  }
  return compactJoin([
    `${counts.checked} ${labels.checked || "checked"}`,
    `${counts.allowed} ${labels.allowed || "allowed"}`,
    `${counts.denied} ${labels.denied || "denied"}`,
    `${counts.approvalRequired} ${labels.approvalRequired || "approval"}`,
  ], " · ");
}

function formatApprovalCounts(counts, labels) {
  if (!counts.requested && !counts.approved && !counts.denied && !counts.expired) {
    return "";
  }
  return compactJoin([
    `${counts.requested} ${labels.requested || "requested"}`,
    `${counts.approved} ${labels.approved || "approved"}`,
    `${counts.denied} ${labels.rejected || "denied"}`,
    `${counts.expired} ${labels.expired || "expired"}`,
  ], " · ");
}

function formatHarnessEvalResult(payload, summary, labels) {
  if (!payload || !Object.keys(payload).length) {
    return "";
  }
  return compactJoin([
    payload.ok === true ? (labels.checkpointPass || "pass") : "fail",
    summary.total_cases !== undefined ? `${summary.passed_cases}/${summary.total_cases} ${labels.cases || "cases"}` : "",
    summary.total_checks !== undefined ? `${summary.passed_checks}/${summary.total_checks} ${labels.checks || "checks"}` : "",
  ], " · ");
}

function formatHarnessScorecard(payload, labels) {
  if (!payload || !Object.keys(payload).length) {
    return "";
  }
  const profile = payload.profile || {};
  const contract = payload.contract || {};
  const completion = payload.completion || {};
  const traceHealth = payload.trace_health || payload.traceHealth || {};
  const sensorCount = Array.isArray(payload.sensors) ? payload.sensors.length : 0;
  return compactJoin([
    profile.name || contract.task_type || contract.taskType,
    completion.status,
    traceHealth.status ? `${labels.traceHealth || "trace"} ${traceHealth.status}` : "",
    sensorCount ? `${sensorCount} ${labels.sensors || "sensors"}` : "",
  ], " · ");
}

function decisionTimelineTitle(item) {
  return props.copy.trace.decisionTimeline.titles?.[item.titleKey] || item.title || item.titleKey;
}

function decisionTimelinePhaseLabel(item) {
  return props.copy.trace.decisionTimeline.phases?.[item.phase] || item.phase;
}

function decisionTimelineDetailLabel(detail) {
  return props.copy.trace.decisionTimeline.details?.[detail.labelKey] || detail.labelKey;
}

function eventSummary(event) {
  const artifact = event.artifact || {};
  if (artifact.title || artifact.detail) {
    return [artifact.title, artifact.detail].filter(Boolean).join(" · ");
  }
  const payload = event.payload || {};
  if (event.eventType === "harness_profile.selected" || event.eventType === "harness_profile.initial_selected" || event.eventType === "harness_profile.effective_selected") {
    return compactJoin([payload.selection_phase || payload.selectionPhase, payload.name, payload.task_type || payload.taskType, payload.reason], " / ");
  }
  if (event.eventType === "harness_profile.changed") {
    return formatHarnessProfileChange(payload);
  }
  if (event.eventType === "harness_policy.selected") {
    return compactJoin([payload.name, `${countPayloadItems(payload.allowed_tools || payload.allowedTools)} tools`, payload.reason], " · ");
  }
  if (event.eventType === "harness_policy.merge_resolved") {
    return compactJoin([
      payload.harness_policy?.name || payload.harnessPolicy?.name,
      `${countPayloadItems(payload.constraints_applied || payload.constraintsApplied)} constraints`,
      `${countPayloadItems(payload.blocked_relaxations || payload.blockedRelaxations)} blocked relaxations`,
    ], " · ");
  }
  if (String(event.eventType || "").startsWith("harness_eval.")) {
    const summary = payload.summary || {};
    return compactJoin([
      payload.kind,
      payload.ok === true ? "pass" : "fail",
      summary.total_cases !== undefined ? `${summary.passed_cases}/${summary.total_cases} cases` : "",
    ], " · ");
  }
  if (event.eventType === "harness_checkpoint.recorded") {
    const completion = payload.completion || {};
    return compactJoin([payload.next_action || payload.nextAction, completion.status, completion.reason], " · ");
  }
  if (event.eventType === "harness_scorecard.recorded") {
    return formatHarnessScorecard(payload, props.copy.trace.harnessLabels || {});
  }
  if (String(event.eventType || "").startsWith("tool_permission.")) {
    return compactJoin([payload.tool_name || payload.toolName, payload.decision, payload.reason], " · ");
  }
  if (String(event.eventType || "").startsWith("tool_approval.")) {
    return compactJoin([payload.tool_name || payload.toolName, payload.status, payload.resolution_reason || payload.resolutionReason || payload.reason], " · ");
  }
  if (event.eventType === "task_contract.created") {
    return compactJoin([
      payload.task_type || payload.taskType,
      `${countPayloadItems(payload.requirements)} req`,
      `${countPayloadItems(payload.acceptance_criteria || payload.acceptanceCriteria)} criteria`,
    ], " · ");
  }
  if (payload.tool_name) {
    return [payload.tool_name, payload.input_delta].filter(Boolean).join(" · ");
  }
  if (payload.action) {
    return payload.action;
  }
  if (payload.status) {
    return payload.status;
  }
  if (payload.reason) {
    return payload.reason;
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

function latestEventPayload(eventType) {
  const event = latestEventByType(eventType);
  return event?.payload && typeof event.payload === "object" ? event.payload : {};
}

function latestPartMetadata(partType) {
  for (let index = parts.value.length - 1; index >= 0; index -= 1) {
    const part = parts.value[index];
    if (part?.partType === partType && part.metadata && typeof part.metadata === "object") {
      return part.metadata;
    }
  }
  return {};
}

function latestEventByType(eventType) {
  for (let index = events.value.length - 1; index >= 0; index -= 1) {
    if (events.value[index]?.eventType === eventType) {
      return events.value[index];
    }
  }
  return null;
}

function latestEventWithPrefix(prefix) {
  for (let index = events.value.length - 1; index >= 0; index -= 1) {
    const event = events.value[index];
    if (String(event?.eventType || "").startsWith(prefix)) {
      return event;
    }
  }
  return null;
}

function countPayloadItems(value) {
  return Array.isArray(value) ? value.length : 0;
}

function compactJoin(values, separator) {
  return values.map((value) => String(value || "").trim()).filter(Boolean).join(separator);
}

function payloadList(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function formatPayloadList(items, maxItems = 3, separator = ", ") {
  const values = payloadList(items);
  if (!values.length) {
    return "";
  }
  const visible = values.slice(0, maxItems).join(separator);
  const remaining = values.length - maxItems;
  return remaining > 0 ? `${visible} +${remaining}` : visible;
}

function formatMissingEvidence(value) {
  return formatPayloadList(payloadList(value).map(previewText), 2, "; ");
}

function formatPolicyRisks(policy, labels) {
  if (!policy || !Object.keys(policy).length) {
    return "";
  }
  const allowed = formatPayloadList(policy.allowed_risk_levels || policy.allowedRiskLevels);
  const denied = formatPayloadList(policy.denied_risk_levels || policy.deniedRiskLevels);
  const approval = formatPayloadList(policy.approval_required_risk_levels || policy.approvalRequiredRiskLevels);
  return compactJoin([
    allowed ? `${labels.policyAllowed || "allow"} ${allowed}` : "",
    denied ? `${labels.policyDenied || "deny"} ${denied}` : "",
    approval ? `${labels.policyApproval || "approval"} ${approval}` : "",
  ], " · ");
}

function formatProfileSelection(selection) {
  if (!selection || !Object.keys(selection).length) {
    return "";
  }
  const signals = formatPayloadList(selection.matched_signals || selection.matchedSignals, 4);
  const selectedBy = previewText(selection.selected_by || selection.selectedBy || "");
  return compactJoin([selectedBy, signals], " · ");
}

function formatHarnessProfilePayload(payload) {
  if (!payload || !Object.keys(payload).length) {
    return "";
  }
  return compactJoin([
    payload.name,
    payload.task_type || payload.taskType,
    payload.selection_phase || payload.selectionPhase,
  ], " / ");
}

function formatHarnessProfileChange(payload) {
  if (!payload || !Object.keys(payload).length) {
    return "";
  }
  const initial = formatHarnessProfilePayload(payload.initial || {});
  const effective = formatHarnessProfilePayload(payload.effective || {});
  return compactJoin([
    initial && effective ? `${initial} -> ${effective}` : "",
    payload.reason,
  ], " / ");
}

function formatCheckpoint(payload, source, labels) {
  if (!payload || !Object.keys(payload).length) {
    return "";
  }
  const passIndex = payload.pass_index ?? payload.passIndex;
  const attempts = payload.auto_continue_attempts ?? payload.autoContinueAttempts;
  return compactJoin([
    passIndex !== undefined && passIndex !== null ? `${labels.checkpointPass || "pass"} ${passIndex}` : "",
    attempts !== undefined && attempts !== null ? `${labels.checkpointAttempts || "attempts"} ${attempts}` : "",
    source === "part" ? (labels.checkpointPart || "durable part") : "",
    source === "event" ? (labels.checkpointEvent || "event") : "",
  ], " · ");
}

function formatOperationAudit(payload) {
  if (!payload || !Object.keys(payload).length) {
    return "";
  }
  return compactJoin([
    payload.operation_type || payload.operationType,
    payload.target,
    payload.rollback_available || payload.rollbackAvailable ? "rollback available" : "",
  ], " · ");
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
  } catch (error) {
    return {
      id: part.partId || `parse-error:${part.createdAt}`,
      action: "parse_error",
      count: 0,
      items: [],
      hiddenCount: 0,
      error: error?.message || props.copy.trace.codeNavigationParseFailed,
    };
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
    hiddenCount: Math.max(0, items.length - normalizedItems.length),
    error: "",
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
  const labels = props.copy.trace.codeNavigationActions || {};
  return labels[result.action] || result.action || props.copy.trace.codeNavigation;
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

function previewText(value, maxLength = 96) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();
  if (!normalized) {
    return "";
  }
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}...` : normalized;
}

function parallelGroupId(artifact) {
  const metadata = artifact?.metadata || {};
  return String(metadata.fanout_group_id || metadata.fanoutGroupId || "").trim();
}

function parallelGroupIndex(artifact) {
  const metadata = artifact?.metadata || {};
  const value = Number(metadata.fanout_index ?? metadata.fanoutIndex ?? 0);
  return Number.isFinite(value) && value > 0 ? value : Number.MAX_SAFE_INTEGER;
}

function parallelGroupTotal(artifact) {
  const metadata = artifact?.metadata || {};
  const value = Number(metadata.fanout_total ?? metadata.fanoutTotal ?? 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function shortenParallelGroupId(groupId) {
  const normalized = String(groupId || "").replace(/^fanout_/, "");
  return normalized.length > 8 ? normalized.slice(0, 8) : normalized || props.copy.trace.unknownArtifact;
}

function countParallelStatuses(statuses) {
  return statuses.reduce((counts, status) => {
    const key = String(status || "unknown").trim() || "unknown";
    counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {});
}

function summarizeParallelGroupStatus(items) {
  const statuses = items.map((item) => String(item.status || "unknown").trim() || "unknown");
  if (statuses.some((status) => status === "running" || status === "cancelling")) {
    return statuses.includes("cancelling") ? "cancelling" : "running";
  }
  if (statuses.some((status) => status === "failed" || status === "error")) {
    return "failed";
  }
  if (statuses.some((status) => status === "cancelled")) {
    return "cancelled";
  }
  if (statuses.every((status) => status === "completed")) {
    return "completed";
  }
  return "unknown";
}

function parallelStatusLabel(status) {
  const labels = props.copy.trace.parallelStatusLabels || {};
  return labels[status] || status || props.copy.trace.unknownArtifact;
}

function parallelGroupCountLabel(group) {
  if (typeof props.copy.trace.parallelTasks === "function") {
    return props.copy.trace.parallelTasks(group.items.length, group.total || group.items.length);
  }
  return `${group.items.length}/${group.total || group.items.length}`;
}

function parallelGroupStatusEntries(group) {
  return Object.entries(group.statusCounts || {})
    .filter(([, count]) => Number(count) > 0)
    .sort((left, right) => left[0].localeCompare(right[0]))
    .map(([status, count]) => ({ status, count, label: parallelStatusLabel(status) }));
}

function parallelTaskTitle(artifact) {
  const metadata = artifact?.metadata || {};
  return String(metadata.prompt_type || metadata.promptType || artifact.title || props.copy.trace.unknownArtifact).trim();
}

function parallelTaskMeta(artifact) {
  const metadata = artifact?.metadata || {};
  return [metadata.task_id || metadata.taskId, metadata.child_run_id || metadata.childRunId].filter(Boolean).join(" · ");
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
  if (artifact.artifactType === "subagent_task") {
    const metadata = artifact.metadata || {};
    return [metadata.task_id || metadata.taskId, metadata.child_run_id || metadata.childRunId].filter(Boolean).join(" · ");
  }
  if (isProcessArtifact(artifact)) {
    const metadata = artifact.metadata || {};
    return [metadata.process_session_id || metadata.processSessionId, artifact.phase].filter(Boolean).join(" · ");
  }
  return artifact.artifactType || "";
}

function artifactDetail(artifact) {
  if (isFileArtifact(artifact)) {
    return artifact.diffLen ? props.copy.trace.diffChars(artifact.diffLen) : artifact.detail;
  }
  if (isStructuredSubagentArtifact(artifact)) {
    return structuredSubagentOutput(artifact).summary || artifact.detail;
  }
  return artifact.detail;
}

function isToolArtifact(artifact) {
  return artifact.kind === "tool";
}

function isFileArtifact(artifact) {
  return (artifact.kind === "file" || Boolean(artifact.path)) && Boolean(artifact.path);
}

function isStructuredSubagentArtifact(artifact) {
  return artifact?.artifactType === "subagent_task" && structuredSubagentOutput(artifact) !== null;
}

function isProcessArtifact(artifact) {
  return artifact?.kind === "process" || artifact?.artifactType === "background_process";
}

function isTaskArtifactSummary(artifact) {
  return artifact?.artifactType === "task_artifacts";
}

function structuredSubagentOutput(artifact) {
  const structured = artifact?.metadata?.structured_output || artifact?.metadata?.structuredOutput;
  return structured && typeof structured === "object" ? structured : null;
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

function toolMetadataSources(artifact) {
  return [artifact.metadata, ...relatedToolParts(artifact).flatMap((part) => [part.metadata, part.artifact?.metadata])]
    .filter((item) => item && typeof item === "object");
}

function toolDetailRows(artifact) {
  const labels = props.copy.trace.detailLabels;
  const sources = toolMetadataSources(artifact);
  const args = firstMetadataRawValue(sources, ["args", "arguments"]);
  const resultPayload = latestToolResultPayload(artifact);
  const provider =
    resultPayload?.provider ||
    resultPayload?.search_provider ||
    firstMetadataValue(sources, ["provider", "search_provider", "searchProvider", "configured_provider", "configuredProvider"]);
  const backend =
    resultPayload?.backend ||
    resultPayload?.search_backend ||
    firstMetadataValue(sources, ["backend", "search_backend", "searchBackend"]);
  const returnedItems = Array.isArray(resultPayload?.items)
    ? resultPayload.items.length
    : firstMetadataValue(sources, ["returned_items", "returnedItems"]);
  const rows = [
    { label: labels.toolCallId, value: artifact.toolCallId },
    { label: labels.iteration, value: artifact.iteration },
    { label: labels.phase, value: artifact.phase },
    { label: labels.query || "Query", value: args?.query || resultPayload?.query || firstMetadataValue(sources, ["query"]) },
    { label: labels.provider || "Provider", value: provider },
    { label: labels.backend || "Backend", value: backend },
    { label: labels.count || "Count", value: args?.count },
    { label: labels.freshness || "Freshness", value: args?.freshness },
    { label: labels.url || "URL", value: args?.url || resultPayload?.url || resultPayload?.final_url || firstMetadataValue(sources, ["url", "final_url", "finalUrl"]) },
    { label: labels.maxChars || "Max chars", value: args?.max_chars ?? args?.maxChars },
    { label: labels.resultLength || "Result length", value: firstMetadataValue(sources, ["result_len", "resultLen"]) },
    { label: labels.returnedItems || "Returned items", value: returnedItems },
    { label: labels.args, value: firstMetadataValue(sources, ["args_preview", "argsPreview", "arguments", "args"]) },
    { label: labels.result, value: firstMetadataValue(sources, ["result_preview", "resultPreview", "result", "output"]) },
    { label: labels.error, value: firstMetadataValue(sources, ["error", "error_preview", "errorPreview"]), tone: "error" },
    { label: labels.detail, value: artifact.detail },
  ];
  return rows.filter((row) => row.value !== "" && row.value !== null && row.value !== undefined);
}

function toolDebugBlocks(artifact) {
  const labels = props.copy.trace.detailLabels;
  const sources = toolMetadataSources(artifact);
  const args = firstMetadataRawValue(sources, ["args", "arguments"]);
  const blocks = [];
  if (args && typeof args === "object") {
    blocks.push({
      key: "args",
      title: labels.argsFull || "Full args",
      meta: summarizeToolArgs(args),
      content: formatMetadata(args),
      open: true,
    });
  }

  const resultPayload = latestToolResultPayload(artifact);
  if (resultPayload) {
    const compactPayload = compactToolResultPayload(artifact, resultPayload);
    blocks.push({
      key: "result",
      title: labels.resultFull || "Result payload",
      meta: summarizeToolResult(resultPayload),
      content: formatMetadata(compactPayload),
      open: true,
    });
  }
  return blocks;
}

function firstMetadataRawValue(sources, keys) {
  for (const source of sources) {
    for (const key of keys) {
      const value = source[key];
      if (value !== "" && value !== null && value !== undefined) {
        return value;
      }
    }
  }
  return null;
}

function latestToolResultPayload(artifact) {
  const toolParts = relatedToolParts(artifact).filter((part) => part.partType === "tool_result" && part.content);
  for (let index = toolParts.length - 1; index >= 0; index -= 1) {
    const payload = parseJsonObject(toolParts[index].content);
    if (payload) {
      return payload;
    }
  }
  for (const candidate of [artifact.detail, artifact.metadata?.result, artifact.metadata?.output, artifact.metadata?.result_preview, artifact.metadata?.resultPreview]) {
    const payload = parseJsonObject(candidate);
    if (payload) {
      return payload;
    }
  }
  return null;
}

function parseJsonObject(value) {
  if (!value) {
    return null;
  }
  if (typeof value === "object") {
    return value;
  }
  try {
    const parsed = JSON.parse(String(value));
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

function compactToolResultPayload(artifact, payload) {
  const type = String(payload.type || artifact.toolName || "").trim();
  if (type === "web_search") {
    const items = Array.isArray(payload.items) ? payload.items : [];
    return {
      type: payload.type,
      provider: payload.provider,
      backend: payload.backend,
      query: payload.query,
      ok: payload.ok,
      returned_items: items.length,
      items: items.slice(0, 10).map((item) => ({
        title: item?.title,
        url: item?.url || item?.final_url || item?.finalUrl,
        snippet: previewText(item?.content || item?.snippet || item?.summary || "", 600),
      })),
      error: payload.error,
    };
  }
  if (type === "web_research") {
    const searchAttempts = Array.isArray(payload.search_attempts) ? payload.search_attempts : [];
    const queryAttempts = Array.isArray(payload.query_attempts) ? payload.query_attempts : [];
    const sources = Array.isArray(payload.sources) ? payload.sources : [];
    return {
      type: payload.type,
      provider: payload.provider,
      backend: payload.backend,
      query: payload.query,
      fetched_count: payload.fetched_count,
      source_count: payload.source_count,
      coverage: payload.coverage || null,
      search_attempts: searchAttempts.map((attempt) => ({
        provider: attempt?.provider,
        configured_provider: attempt?.configured_provider,
        backend: attempt?.backend,
        ok: attempt?.ok,
        result_count: attempt?.result_count,
        fetchable_count: attempt?.fetchable_count,
        error: attempt?.error,
      })),
      query_attempts: queryAttempts.map((attempt) => ({
        query: attempt?.query,
        provider: attempt?.provider,
        backend: attempt?.backend,
        ok: attempt?.ok,
        result_count: attempt?.result_count,
      })),
      sources: sources.slice(0, 10).map((source) => ({
        tool_name: source?.tool_name,
        title: source?.title,
        url: source?.url,
        search_provider: source?.search_provider,
        search_backend: source?.search_backend,
        search_rank: source?.search_rank,
      })),
      error: payload.error,
    };
  }
  if (type === "web_fetch") {
    const content = String(payload.content || "");
    return {
      type: payload.type,
      url: payload.url,
      final_url: payload.final_url || payload.finalUrl,
      title: payload.title,
      ok: payload.ok,
      content_chars: content.length,
      content_preview: previewText(content, 3200),
      error: payload.error,
    };
  }
  return payload;
}

function summarizeToolArgs(args) {
  if (!args || typeof args !== "object") {
    return "";
  }
  return compactJoin([
    args.query,
    args.url,
    args.count !== undefined ? `count ${args.count}` : "",
    args.freshness ? `freshness ${args.freshness}` : "",
    args.max_chars !== undefined || args.maxChars !== undefined ? `max ${args.max_chars ?? args.maxChars}` : "",
  ], " / ");
}

function summarizeToolResult(payload) {
  if (!payload || typeof payload !== "object") {
    return "";
  }
  const items = Array.isArray(payload.items) ? `${payload.items.length} items` : "";
  const content = payload.content ? `${String(payload.content).length} chars` : "";
  return compactJoin([payload.provider, payload.backend, items, content, payload.final_url || payload.finalUrl || payload.url], " / ");
}

function subagentDetailRows(artifact) {
  const labels = props.copy.trace.detailLabels;
  const structured = structuredSubagentOutput(artifact) || {};
  const rows = [
    { label: labels.structuredStatus, value: structured.status },
    { label: labels.structuredSections, value: structured.section_count ?? structured.sectionCount },
    { label: labels.structuredFindings, value: structured.finding_count ?? structured.findingCount },
    { label: labels.structuredQuestions, value: structured.question_count ?? structured.questionCount },
    { label: labels.structuredResidualRisks, value: structured.residual_risk_count ?? structured.residualRiskCount },
    { label: labels.structuredSources, value: structured.source_count ?? structured.sourceCount },
  ];
  return rows.filter((row) => row.value !== "" && row.value !== null && row.value !== undefined);
}

function processDetailRows(artifact) {
  const labels = props.copy.trace.detailLabels;
  const metadata = artifact.metadata || {};
  const rows = [
    { label: labels.processSessionId, value: metadata.process_session_id || metadata.processSessionId || artifact.sourceId },
    { label: labels.command, value: metadata.command || artifact.title },
    { label: labels.cwd, value: metadata.cwd },
    { label: labels.pid, value: metadata.pid },
    { label: labels.state, value: metadata.state || artifact.status },
    { label: labels.termination, value: metadata.termination_reason || metadata.terminationReason },
    { label: labels.exitCode, value: metadata.exit_code ?? metadata.exitCode, tone: Number(metadata.exit_code ?? metadata.exitCode ?? 0) === 0 ? "neutral" : "error" },
    { label: labels.notifyMode, value: metadata.notify_mode || metadata.notifyMode },
    { label: labels.outputTail, value: metadata.output_tail || metadata.outputTail },
    { label: labels.outputPath, value: metadata.output_path || metadata.outputPath },
  ];
  return rows.filter((row) => row.value !== "" && row.value !== null && row.value !== undefined);
}

function taskArtifactRows(artifact) {
  const labels = props.copy.trace.detailLabels;
  const metadata = artifact.metadata || {};
  const rows = [
    { label: labels.taskArtifactCount, value: metadata.count },
    { label: labels.structuredSources, value: taskArtifactSources(artifact).length },
  ];
  return rows.filter((row) => row.value !== "" && row.value !== null && row.value !== undefined);
}

function taskArtifactSources(artifact) {
  const artifacts = Array.isArray(artifact?.metadata?.artifacts) ? artifact.metadata.artifacts : [];
  return artifacts.flatMap((taskArtifact) => {
    const metadata = taskArtifact?.metadata || {};
    const sources = Array.isArray(metadata.sources) ? metadata.sources : [];
    return sources.map((source) => normalizeTaskArtifactSource(source, taskArtifact)).filter(Boolean);
  }).slice(0, 8);
}

function normalizeTaskArtifactSource(source, taskArtifact) {
  if (!source || typeof source !== "object") {
    return null;
  }
  const url = String(source.url || "").trim();
  const title = String(source.title || "").trim();
  const snippet = previewText(source.snippet || source.content || "");
  if (!url && !title && !snippet) {
    return null;
  }
  const provider = String(source.provider || "").trim();
  const backend = String(source.search_backend || source.backend || "").trim();
  const toolName = String(source.tool_name || source.toolName || taskArtifact?.source_tool || taskArtifact?.sourceTool || "").trim();
  return {
    url,
    title,
    snippet,
    domain: sourceDomain(url),
    backend,
    meta: [toolName, provider].filter(Boolean).join(" · "),
  };
}

function sourceDomain(url) {
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function structuredSubagentPreview(artifact) {
  const structured = structuredSubagentOutput(artifact);
  if (!structured) {
    return "";
  }
  const lines = [];
  for (const section of Array.isArray(structured.sections) ? structured.sections.slice(0, 6) : []) {
    const title = String(section?.title || section?.key || "Section").trim();
    const items = Array.isArray(section?.items) ? section.items.slice(0, 6) : [];
    if (!title && !items.length) {
      continue;
    }
    if (title) {
      lines.push(`## ${title}`);
    }
    for (const item of items) {
      const preview = structuredSubagentItemPreview(item);
      if (preview) {
        lines.push(preview);
      }
    }
    lines.push("");
  }
  const questions = Array.isArray(structured.questions) ? structured.questions.slice(0, 4) : [];
  if (questions.length) {
    lines.push("## Questions");
    lines.push(...questions.map((question) => `- ${question}`));
    lines.push("");
  }
  const residualRisks = Array.isArray(structured.residual_risks || structured.residualRisks)
    ? (structured.residual_risks || structured.residualRisks).slice(0, 4)
    : [];
  if (residualRisks.length) {
    lines.push("## Residual Risks");
    lines.push(...residualRisks.map((risk) => `- ${risk}`));
  }
  return lines.join("\n").trim();
}

function structuredSubagentItemPreview(item) {
  if (typeof item === "string") {
    return `- ${item}`;
  }
  if (!item || typeof item !== "object") {
    return "";
  }
  const title = String(item.title || item.name || item.path || "").trim();
  const severity = String(item.severity || "").trim();
  const path = String(item.path || "").trim();
  const why = String(item.why || "").trim();
  const fix = String(item.fix || "").trim();
  const summary = [severity ? `[${severity}]` : "", title || path].filter(Boolean).join(" ").trim();
  const lines = [summary ? `- ${summary}` : `- ${JSON.stringify(item)}`];
  if (why) {
    lines.push(`  Why: ${why}`);
  }
  if (fix) {
    lines.push(`  Fix: ${fix}`);
  }
  return lines.join("\n");
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

function downloadDebugBundle() {
  if (!props.run) {
    return;
  }
  const bundle = buildDebugBundle();
  const blob = new Blob([stringifyDebugBundle(bundle)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `opensprite-debug-${safeFileName(props.run.runId)}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function buildDebugBundle() {
  return {
    schema_version: 1,
    exported_at: new Date().toISOString(),
    run: {
      run_id: props.run.runId,
      session_id: props.run.sessionId,
      status: props.run.status,
      created_at: props.run.createdAt,
      updated_at: props.run.updatedAt,
      finished_at: props.run.finishedAt,
      trace_loaded: Boolean(props.run.traceLoaded),
      trace_loading: Boolean(props.run.traceLoading),
      trace_error: props.run.traceError || "",
      event_counts: props.run.eventCounts || {},
    },
    summary: props.run.summary || null,
    diff_summary: props.run.diffSummary || null,
    worktree_sandbox: props.run.worktreeSandbox || null,
    file_changes: props.run.fileChanges || [],
    artifacts: artifacts.value,
    parts: parts.value,
    events: (props.run.rawEvents || []).length ? props.run.rawEvents : events.value,
    localized_events: props.run.events || [],
  };
}

function stringifyDebugBundle(bundle) {
  const replacer = (key, value) => {
    if (typeof value === "bigint") {
      return value.toString();
    }
    return value;
  };
  try {
    return JSON.stringify(bundle, replacer, 2);
  } catch {
    const seen = new WeakSet();
    return JSON.stringify(
      bundle,
      (key, value) => {
        if (typeof value === "bigint") {
          return value.toString();
        }
        if (value && typeof value === "object") {
          if (seen.has(value)) {
            return "[Circular]";
          }
          seen.add(value);
        }
        return value;
      },
      2,
    );
  }
}

function safeFileName(value) {
  const normalized = String(value || "run").replace(/^run[_-]?/, "");
  return normalized.replace(/[^a-zA-Z0-9._-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64) || "run";
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
