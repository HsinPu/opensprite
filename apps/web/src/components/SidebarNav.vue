<template>
  <aside class="sidebar" id="sidebar" :aria-label="copy.sidebar.ariaLabel">
    <div class="sidebar__top">
      <div class="brand-row">
        <div class="brand-mark" aria-hidden="true">OS</div>
        <div>
          <strong>OpenSprite</strong>
          <span>{{ copy.sidebar.brandSubtitle }}</span>
        </div>
      </div>

      <button class="new-chat-button" type="button" @click="$emit('create-new-chat')">
        <span aria-hidden="true">+</span>
        {{ copy.sidebar.newChat }}
      </button>

      <section class="sidebar__section">
        <div class="sidebar__section-head">
          <span>{{ copy.sidebar.chats }}</span>
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
            <strong>{{ getSessionTitle(session) }}</strong>
            <span>{{ getSessionDisplayId(session) }}</span>
          </button>
        </div>
      </section>
    </div>

    <div class="sidebar__bottom">
      <button class="settings-button" type="button" @click="$emit('open-settings')">
        <span class="settings-button__avatar" aria-hidden="true">OS</span>
        <span>
          <strong>{{ copy.sidebar.settings }}</strong>
          <small>{{ copy.sidebar.settingsSubtitle }}</small>
        </span>
      </button>
    </div>
  </aside>
</template>

<script setup>
defineProps({
  copy: {
    type: Object,
    required: true,
  },
  state: {
    type: Object,
    required: true,
  },
  getSessionDisplayId: {
    type: Function,
    required: true,
  },
  getSessionTitle: {
    type: Function,
    required: true,
  },
});

defineEmits(["create-new-chat", "set-active-session", "open-settings"]);
</script>
