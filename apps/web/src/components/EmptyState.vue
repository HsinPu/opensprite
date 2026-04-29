<template>
  <section class="empty-state" :aria-label="copy.empty.ariaLabel">
    <div class="empty-state__mark" aria-hidden="true">OS</div>
    <h1>{{ copy.empty.title }}</h1>
    <p>{{ copy.empty.description }}</p>
    <div class="prompt-grid">
      <button
        v-for="prompt in prompts"
        :key="prompt.title"
        class="prompt-card"
        type="button"
        @click="$emit('apply-prompt', prompt.text)"
      >
        <strong>{{ prompt.title }}</strong>
        <span>{{ prompt.description }}</span>
      </button>
    </div>
  </section>
</template>

<script setup>
defineProps({
  copy: {
    type: Object,
    required: true,
  },
  prompts: {
    type: Array,
    required: true,
  },
});

defineEmits(["apply-prompt"]);
</script>
