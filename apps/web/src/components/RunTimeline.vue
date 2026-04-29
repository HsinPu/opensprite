<template>
  <section class="run-timeline" :data-tone="summary.tone" :data-collapsed="!expanded" aria-live="polite">
    <div class="run-timeline__header">
      <div class="run-timeline__title">
        <span class="run-timeline__eyebrow">{{ copy.timeline.runPrefix }} {{ summary.shortId }}</span>
        <strong>{{ summary.title }}</strong>
      </div>
      <div class="run-timeline__actions">
        <span class="run-timeline__status">{{ summary.statusLabel }}</span>
        <button class="run-block-toggle" type="button" :aria-expanded="expanded" @click="expanded = !expanded">
          {{ expanded ? copy.timeline.collapse : copy.timeline.expand }}
        </button>
      </div>
    </div>

    <ol v-show="expanded" class="run-timeline__list">
      <li
        v-for="event in events"
        :key="event.id"
        class="run-timeline__item"
        :data-tone="event.tone"
      >
        <span class="run-timeline__dot" aria-hidden="true"></span>
        <div class="run-timeline__text">
          <strong>{{ event.label }}</strong>
          <span v-if="event.detail">{{ event.detail }}</span>
        </div>
        <time>{{ formatEventTime(event.createdAt) }}</time>
      </li>
    </ol>
  </section>
</template>

<script setup>
import { ref } from "vue";

import { formatEventTime } from "../composables/useChatClient";

const expanded = ref(false);

defineProps({
  copy: {
    type: Object,
    required: true,
  },
  summary: {
    type: Object,
    required: true,
  },
  events: {
    type: Array,
    required: true,
  },
});
</script>
