<template>
  <div class="message-list">
    <article
      v-for="message in messages"
      :key="message.id"
      class="message"
      :class="`message--${message.role}`"
    >
      <div class="message__avatar">{{ message.role === "user" ? copy.message.userAvatar : copy.message.assistantAvatar }}</div>
      <div class="message__content">
        <div class="message__meta">
          {{ message.meta || (message.role === "user" ? displayName : "OpenSprite") }}
        </div>
        <div class="message__bubble">{{ message.text }}</div>
      </div>
    </article>
  </div>
</template>

<script setup>
defineProps({
  copy: {
    type: Object,
    required: true,
  },
  messages: {
    type: Array,
    required: true,
  },
  displayName: {
    type: String,
    required: true,
  },
});
</script>
