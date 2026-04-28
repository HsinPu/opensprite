<template>
  <section v-if="run" class="run-trace" aria-label="Run trace viewer">
    <header class="run-trace__header">
      <div>
        <span class="run-trace__eyebrow">Trace</span>
        <strong>{{ run.runId }}</strong>
      </div>
      <div class="run-trace__actions">
        <span class="run-trace__status" :data-status="run.status">{{ run.status }}</span>
        <button
          v-if="run.status === 'running'"
          class="run-trace__cancel"
          type="button"
          :disabled="run.cancelPending"
          @click="$emit('cancel-run', run)"
        >
          {{ run.cancelPending ? "Cancelling" : "Cancel run" }}
        </button>
      </div>
    </header>

    <div class="run-trace__summary" aria-label="Run event summary">
      <span>{{ events.length }} events</span>
      <span>{{ toolEventCount }} tool</span>
      <span>{{ verificationEventCount }} verification</span>
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
        :data-category="eventCategory(event.eventType)"
      >
        <summary>
          <span class="run-trace__event-type">{{ event.eventType }}</span>
          <span v-if="eventSummary(event)" class="run-trace__event-summary">{{ eventSummary(event) }}</span>
          <time>{{ formatEventTime(event.createdAt) }}</time>
        </summary>
        <pre>{{ formatPayload(event.payload) }}</pre>
      </details>

      <p v-if="filteredEvents.length === 0" class="run-trace__empty">No events match this filter.</p>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from "vue";

import { formatEventTime } from "../composables/useChatClient";

const props = defineProps({
  run: {
    type: Object,
    default: null,
  },
});

defineEmits(["cancel-run"]);

const selectedFilter = ref("all");

const events = computed(() => props.run?.rawEvents || props.run?.events || []);

const filteredEvents = computed(() => {
  if (selectedFilter.value === "all") {
    return events.value;
  }
  return events.value.filter((event) => eventCategory(event.eventType) === selectedFilter.value);
});

const toolEventCount = computed(() => countEventsByCategory("tool"));
const verificationEventCount = computed(() => countEventsByCategory("verification"));

const filterOptions = computed(() => [
  { value: "all", label: "All", count: events.value.length },
  { value: "run", label: "Run", count: countEventsByCategory("run") },
  { value: "llm", label: "LLM", count: countEventsByCategory("llm") },
  { value: "tool", label: "Tools", count: toolEventCount.value },
  { value: "verification", label: "Verify", count: verificationEventCount.value },
  { value: "other", label: "Other", count: countEventsByCategory("other") },
]);

function countEventsByCategory(category) {
  return events.value.filter((event) => eventCategory(event.eventType) === category).length;
}

function eventCategory(eventType) {
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

function formatPayload(payload) {
  try {
    return JSON.stringify(payload || {}, null, 2);
  } catch {
    return String(payload || "");
  }
}
</script>
