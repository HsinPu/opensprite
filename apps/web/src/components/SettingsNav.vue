<template>
  <aside class="settings-nav" aria-label="Settings sections">
    <div v-for="group in groups" :key="group.label" class="settings-nav__group">
      <p>{{ group.label }}</p>
      <button
        v-for="item in group.items"
        :key="item.section"
        class="settings-nav__item"
        :class="{ 'settings-nav__item--active': section === item.section }"
        type="button"
        @click="$emit('select-section', item.section)"
      >
        <span aria-hidden="true">{{ item.icon }}</span>
        {{ item.title }}
      </button>
    </div>

    <div class="settings-nav__footer">
      <strong>OpenSprite Web</strong>
      <span>{{ copy.settings.version }}</span>
    </div>
  </aside>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  section: {
    type: String,
    required: true,
  },
});

defineEmits(["select-section"]);

const groups = computed(() => [
  {
    label: props.copy.settings.web,
    items: [
      { section: "general", icon: "⌘", title: props.copy.settingsTitles.general },
      { section: "shortcuts", icon: "⌗", title: props.copy.settingsTitles.shortcuts },
      { section: "curator", icon: "◌", title: props.copy.settingsTitles.curator },
    ],
  },
  {
    label: props.copy.settings.server,
    items: [
      { section: "providers", icon: "⚙", title: props.copy.settingsTitles.providers },
      { section: "models", icon: "✦", title: props.copy.settingsTitles.models },
      { section: "channels", icon: "☷", title: props.copy.settingsTitles.channels },
      { section: "mcp", icon: "◇", title: props.copy.settingsTitles.mcp },
      { section: "schedule", icon: "◷", title: props.copy.settingsTitles.schedule },
      { section: "network", icon: "⇄", title: props.copy.settingsTitles.network },
      { section: "search", icon: "⌕", title: props.copy.settingsTitles.search },
      { section: "browser", icon: "◉", title: props.copy.settingsTitles.browser },
      { section: "log", icon: "≋", title: props.copy.settingsTitles.log },
    ],
  },
]);
</script>
