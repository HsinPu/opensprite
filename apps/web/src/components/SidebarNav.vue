<template>
  <aside class="sidebar" id="sidebar" aria-label="Chat navigation">
    <div class="sidebar__top">
      <div class="brand-row">
        <div class="brand-mark" aria-hidden="true">OS</div>
        <div>
          <strong>OpenSprite</strong>
          <span>Local assistant</span>
        </div>
      </div>

      <button class="new-chat-button" type="button" @click="$emit('create-new-chat')">
        <span aria-hidden="true">+</span>
        New chat
      </button>

      <section class="sidebar__section">
        <div class="sidebar__section-head">
          <span>Chats</span>
          <small>{{ state.sessions.length }}</small>
        </div>
        <div class="session-list">
          <button
            v-for="session in state.sessions"
            :key="session.externalChatId"
            class="session-tile"
            :class="{ 'session-tile--active': session.externalChatId === state.activeExternalChatId }"
            type="button"
            @click="$emit('set-active-session', session.externalChatId)"
          >
            <strong>{{ session.title }}</strong>
            <span>{{ getSessionDisplayId(session) }}</span>
          </button>
        </div>
      </section>
    </div>

    <div class="sidebar__bottom">
      <button class="settings-button" type="button" @click="$emit('open-settings')">
        <span class="settings-button__avatar" aria-hidden="true">OS</span>
        <span>
          <strong>Settings</strong>
          <small>Preferences and server</small>
        </span>
      </button>
    </div>
  </aside>
</template>

<script setup>
defineProps({
  state: {
    type: Object,
    required: true,
  },
  getSessionDisplayId: {
    type: Function,
    required: true,
  },
});

defineEmits(["create-new-chat", "set-active-session", "open-settings"]);
</script>
