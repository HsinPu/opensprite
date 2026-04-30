<template>
  <aside class="sidebar" id="sidebar" :aria-label="copy.sidebar.ariaLabel">
    <div class="sidebar__top">
      <div class="brand-row">
        <div class="brand-mark" aria-hidden="true">OS</div>
        <div class="brand-row__copy">
          <strong>OpenSprite</strong>
          <span>{{ copy.sidebar.brandSubtitle }}</span>
        </div>
        <button
          class="sidebar-collapse-button"
          type="button"
          :aria-label="collapsed ? copy.sidebar.expand : copy.sidebar.collapse"
          :title="collapsed ? copy.sidebar.expand : copy.sidebar.collapse"
          :aria-pressed="String(collapsed)"
          @click="$emit('toggle-sidebar-collapsed')"
        >
          <span aria-hidden="true">{{ collapsed ? '>' : '<' }}</span>
        </button>
      </div>

      <button class="new-chat-button" type="button" :title="copy.sidebar.newChat" @click="$emit('create-new-chat')">
        <span aria-hidden="true">+</span>
        <span class="new-chat-button__label">{{ copy.sidebar.newChat }}</span>
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
            :title="`${getSessionTitle(session)} · ${getSessionDisplayId(session)}`"
            @click="$emit('set-active-session', session.externalChatId)"
          >
            <span class="session-tile__initial" aria-hidden="true">{{ getSessionTitle(session).slice(0, 1) }}</span>
            <span class="session-tile__heading">
              <strong>{{ getSessionTitle(session) }}</strong>
              <span v-if="session.channel && session.channel !== 'web'" class="session-tile__channel">
                {{ session.channel }} · read-only
              </span>
            </span>
            <span class="session-tile__id">{{ getSessionDisplayId(session) }}</span>
          </button>
        </div>
      </section>
    </div>

    <div class="sidebar__bottom">
      <button class="settings-button" type="button" :title="copy.sidebar.settings" @click="$emit('open-settings')">
        <span class="settings-button__avatar" aria-hidden="true">OS</span>
        <span class="settings-button__copy">
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
  collapsed: {
    type: Boolean,
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

defineEmits(["create-new-chat", "set-active-session", "toggle-sidebar-collapsed", "open-settings"]);
</script>
