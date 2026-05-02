<template>
  <section class="work-state-card" :data-status="workState.status" aria-live="polite">
    <div class="work-state-card__header">
      <div class="work-state-card__title">
        <span class="work-state-card__eyebrow">{{ copy.workState.title }}</span>
        <strong>{{ workState.objective }}</strong>
      </div>
      <span class="work-state-card__status">{{ statusLabel }}</span>
    </div>

    <dl class="work-state-card__grid">
      <div>
        <dt>{{ copy.workState.currentStep }}</dt>
        <dd>{{ displayStep(workState.currentStep) }}</dd>
      </div>
      <div>
        <dt>{{ copy.workState.nextStep }}</dt>
        <dd>{{ displayStep(workState.nextStep) }}</dd>
      </div>
      <div>
        <dt>{{ copy.workState.verification }}</dt>
        <dd>{{ verificationLabel }}</dd>
      </div>
      <div>
        <dt>{{ copy.workState.files }}</dt>
        <dd>{{ copy.workState.fileCount(workState.fileChangeCount) }}</dd>
      </div>
    </dl>

    <div v-if="workState.blockers.length" class="work-state-card__note" data-tone="warning">
      <strong>{{ copy.workState.blockers }}</strong>
      <span>{{ workState.blockers[0] }}</span>
    </div>

    <div v-else-if="workState.resumeHint" class="work-state-card__note">
      <strong>{{ copy.workState.resumeHint }}</strong>
      <span>{{ workState.resumeHint }}</span>
    </div>

    <div v-if="delegatedTaskCount" class="work-state-card__note">
      <strong>{{ copy.workState.delegatedTask }}</strong>
      <span>{{ delegatedTaskSummary }}</span>
      <small v-if="delegatedTaskCount > 1">{{ copy.workState.moreDelegates(delegatedTaskCount - 1) }}</small>
    </div>

    <div v-if="visibleTouchedPaths.length" class="work-state-card__paths">
      <span>{{ copy.workState.touchedPaths }}</span>
      <code v-for="path in visibleTouchedPaths" :key="path">{{ path }}</code>
      <small v-if="hiddenTouchedPathCount > 0">{{ copy.workState.morePaths(hiddenTouchedPathCount) }}</small>
    </div>
  </section>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  workState: {
    type: Object,
    required: true,
  },
});

const statusLabel = computed(() => {
  return props.copy.workState.statusLabels[props.workState.status] || props.workState.status;
});

const verificationLabel = computed(() => {
  if (props.workState.verificationPassed) {
    return props.copy.workState.verificationPassed;
  }
  if (props.workState.verificationAttempted) {
    return props.copy.workState.verificationFailed;
  }
  if (props.workState.expectsVerification) {
    return props.copy.workState.verificationPending;
  }
  return props.copy.workState.verificationNotRequired;
});

const delegatedTasks = computed(() => Array.isArray(props.workState.delegatedTasks) ? props.workState.delegatedTasks : []);

const selectedDelegatedTask = computed(() => delegatedTasks.value.find((task) => task.selected) || null);

const delegatedTaskCount = computed(() => delegatedTasks.value.length);

const delegatedTaskSummary = computed(() => {
  if (selectedDelegatedTask.value) {
    const promptType = selectedDelegatedTask.value.promptType || props.copy.workState.unknownDelegate;
    return `${promptType} (${selectedDelegatedTask.value.taskId})`;
  }
  return props.copy.workState.delegateCount(delegatedTaskCount.value);
});

const visibleTouchedPaths = computed(() => props.workState.touchedPaths.slice(0, 3));

const hiddenTouchedPathCount = computed(() => {
  return Math.max(0, props.workState.touchedPaths.length - visibleTouchedPaths.value.length);
});

function displayStep(value) {
  const text = String(value || "").trim();
  return text && text !== "not set" ? text : props.copy.workState.noStep;
}
</script>
