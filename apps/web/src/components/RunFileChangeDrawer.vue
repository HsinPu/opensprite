<template>
  <div class="run-file-drawer" role="presentation" @click.self="$emit('close')">
    <aside class="run-file-drawer__panel" role="dialog" aria-modal="true" :aria-label="copy.runFileInspector.title">
      <header class="run-file-drawer__header">
        <div>
          <span>{{ copy.runFileInspector.title }}</span>
          <strong>{{ inspectedChange.path }}</strong>
        </div>
        <button class="run-file-drawer__close" type="button" :aria-label="copy.runFileInspector.close" @click="$emit('close')">
          x
        </button>
      </header>

      <dl class="run-file-drawer__meta">
        <div>
          <dt>{{ copy.runFileInspector.action }}</dt>
          <dd>{{ inspectedChange.action || copy.runFileInspector.unknown }}</dd>
        </div>
        <div>
          <dt>{{ copy.runFileInspector.tool }}</dt>
          <dd>{{ inspectedChange.toolName || copy.runFileInspector.unknown }}</dd>
        </div>
        <div>
          <dt>{{ copy.runFileInspector.run }}</dt>
          <dd>{{ run.runId }}</dd>
        </div>
        <div>
          <dt>{{ copy.runFileInspector.session }}</dt>
          <dd>{{ run.sessionId || copy.runFileInspector.unknown }}</dd>
        </div>
      </dl>

      <p v-if="run.traceLoading" class="run-file-drawer__notice">
        {{ copy.runFileInspector.loadingSnapshots }}
      </p>
      <p v-else-if="run.traceError" class="run-file-drawer__notice" data-tone="error">
        {{ copy.runFileInspector.traceUnavailable }}: {{ run.traceError }}
      </p>

      <section class="run-file-drawer__section">
        <h3>{{ copy.runFileInspector.diff }}</h3>
        <pre v-if="inspectedChange.diff || inspectedChange.diffPreview">{{ inspectedChange.diff || inspectedChange.diffPreview }}</pre>
        <p v-else>{{ copy.runFileInspector.noDiff }}</p>
      </section>

      <section class="run-file-drawer__section">
        <h3>{{ copy.runFileInspector.snapshots }}</h3>
        <div class="run-file-drawer__snapshot-grid">
          <details class="run-file-drawer__snapshot" :open="hasBeforeContent">
            <summary>
              {{ copy.runFileInspector.before }}
              <span>{{ beforeSnapshotLabel }}</span>
            </summary>
            <pre v-if="hasBeforeContent">{{ inspectedChange.beforeContent }}</pre>
            <p v-else>{{ copy.runFileInspector.snapshotUnavailable }}</p>
          </details>

          <details class="run-file-drawer__snapshot" :open="hasAfterContent">
            <summary>
              {{ copy.runFileInspector.after }}
              <span>{{ afterSnapshotLabel }}</span>
            </summary>
            <pre v-if="hasAfterContent">{{ inspectedChange.afterContent }}</pre>
            <p v-else>{{ copy.runFileInspector.snapshotUnavailable }}</p>
          </details>
        </div>
      </section>
    </aside>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  run: {
    type: Object,
    required: true,
  },
  change: {
    type: Object,
    required: true,
  },
});

defineEmits(["close"]);

const richChange = computed(() => {
  const changes = props.run.fileChanges || [];
  return changes.find((item) => {
    if (props.change.changeId && item.changeId) {
      return item.changeId === props.change.changeId;
    }
    return item.path === props.change.path && (!props.change.action || item.action === props.change.action);
  }) || null;
});

const inspectedChange = computed(() => ({
  ...props.change,
  ...(richChange.value || {}),
  snapshotsAvailable: {
    ...(props.change.snapshotsAvailable || {}),
    ...(richChange.value?.snapshotsAvailable || {}),
  },
}));

const hasBeforeContent = computed(() => inspectedChange.value.beforeContent !== null && inspectedChange.value.beforeContent !== undefined);
const hasAfterContent = computed(() => inspectedChange.value.afterContent !== null && inspectedChange.value.afterContent !== undefined);

const beforeSnapshotLabel = computed(() => snapshotLabel(hasBeforeContent.value, inspectedChange.value.snapshotsAvailable?.before));
const afterSnapshotLabel = computed(() => snapshotLabel(hasAfterContent.value, inspectedChange.value.snapshotsAvailable?.after));

function snapshotLabel(hasContent, available) {
  if (hasContent) {
    return props.copy.runFileInspector.loaded;
  }
  if (available || props.run.traceLoading) {
    return props.copy.runFileInspector.available;
  }
  return props.copy.runFileInspector.unavailable;
}
</script>
