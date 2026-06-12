<template>
  <aside class="sidebar" id="sidebar" :aria-label="copy.sidebar.ariaLabel">
    <div class="sidebar__top">
      <div class="brand-row">
        <button
          class="brand-mark brand-mark--button"
          type="button"
          :aria-label="collapsed ? copy.sidebar.expand : 'OpenSprite'"
          :title="collapsed ? copy.sidebar.expand : 'OpenSprite'"
          :disabled="!collapsed"
          @click="collapsed && $emit('toggle-sidebar-collapsed')"
        >
          <span class="brand-mark__initial" aria-hidden="true">OS</span>
          <span class="brand-mark__expand" aria-hidden="true"></span>
        </button>
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
          <span class="sidebar-collapse-button__icon" aria-hidden="true"></span>
        </button>
      </div>

      <button class="new-chat-button" type="button" :title="copy.sidebar.newChat" @click="$emit('create-new-chat')">
        <span aria-hidden="true">+</span>
        <span class="new-chat-button__label">{{ copy.sidebar.newChat }}</span>
      </button>

      <section class="sidebar__section">
        <div class="sidebar__section-head">
          <span>{{ copy.sidebar.chats }}</span>
          <span class="sidebar__section-meta">
            <small>{{ sessions.length }}/{{ state.sessions.length }}</small>
            <span class="sidebar__section-actions">
              <button
                v-if="!deleteMode"
                class="sidebar__manage-button"
                type="button"
                :disabled="sessions.length === 0"
                :title="copy.sidebar.deleteChat"
                @click="beginDeleteMode"
              >
                {{ copy.sidebar.deleteChat }}
              </button>
              <template v-else>
                <button class="sidebar__manage-button" type="button" @click="cancelDeleteMode">
                  {{ copy.sidebar.cancelDelete }}
                </button>
                <button
                  class="sidebar__manage-button sidebar__manage-button--danger"
                  type="button"
                  :disabled="selectedSessions.length === 0"
                  @click="deleteSelectedSessions"
                >
                  {{ copy.sidebar.deleteSelectedChats(selectedSessions.length) }}
                </button>
              </template>
            </span>
          </span>
        </div>
        <div class="session-filter" role="group" :aria-label="copy.sidebar.chats">
          <button
            type="button"
            :aria-pressed="String(sessionChannelFilter === 'all')"
            @click="$emit('set-session-channel-filter', 'all')"
          >
            {{ copy.sidebar.filters.all }}
          </button>
          <button
            type="button"
            :aria-pressed="String(sessionChannelFilter === 'web')"
            @click="$emit('set-session-channel-filter', 'web')"
          >
            {{ copy.sidebar.filters.web }}
          </button>
        </div>
        <label class="session-history-toggle" :title="copy.sidebar.showHiddenSessionsTitle">
          <input
            type="checkbox"
            :checked="showHiddenSessions"
            @change="$emit('set-show-hidden-sessions', $event.target.checked)"
          />
          <span aria-hidden="true"></span>
          <strong>{{ copy.sidebar.showHiddenSessions }}</strong>
        </label>
        <div class="session-list">
          <div
            v-for="session in sessions"
            :key="session.externalChatId"
            class="session-tile-wrap"
            :class="{
              'session-tile--active': session.externalChatId === state.activeExternalChatId,
              'session-tile-wrap--selecting': deleteMode,
            }"
          >
            <label v-if="deleteMode" class="session-tile__select" @click.stop>
              <input
                type="checkbox"
                :aria-label="copy.sidebar.selectChat(getSessionTitle(session))"
                :checked="selectedSessionIds.has(sessionSelectionKey(session))"
                @change="toggleSessionSelection(session, $event.target.checked)"
              />
              <span aria-hidden="true"></span>
            </label>
            <button
              class="session-tile"
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
        </div>
      </section>

    </div>

    <div class="sidebar__bottom">
      <BackgroundProcessSidebar
        :copy="copy"
        :processes="backgroundProcesses.processes"
        :loading="backgroundProcesses.loading"
        :error="backgroundProcesses.error"
        :collapsed="collapsed"
        :active-session-id="activeSessionId"
        @select-session="$emit('select-background-process', $event)"
        @select-run="$emit('select-background-process', $event)"
        @refresh="$emit('refresh-background-processes')"
      />

      <button class="settings-button" type="button" :title="copy.sidebar.settings" @click="$emit('open-settings')">
        <span class="settings-button__avatar" aria-hidden="true">OS</span>
        <span class="settings-button__copy">
          <strong>{{ copy.sidebar.settings }}</strong>
          <small>{{ copy.sidebar.settingsSubtitle }}</small>
        </span>
      </button>
    </div>
    <button
      v-show="!collapsed"
      class="sidebar__resize"
      type="button"
      :aria-label="copy.sidebar.resizeSidebar"
      :title="copy.sidebar.resizeSidebar"
      @pointerdown="$emit('begin-sidebar-resize', $event)"
    ></button>
  </aside>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import BackgroundProcessSidebar from "./BackgroundProcessSidebar.vue";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  state: {
    type: Object,
    required: true,
  },
  sessions: {
    type: Array,
    required: true,
  },
  sessionChannelFilter: {
    type: String,
    required: true,
  },
  showHiddenSessions: {
    type: Boolean,
    required: true,
  },
  collapsed: {
    type: Boolean,
    required: true,
  },
  backgroundProcesses: {
    type: Object,
    required: true,
  },
  activeSessionId: {
    type: String,
    default: "",
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

const emit = defineEmits([
  "create-new-chat",
  "delete-sessions",
  "set-active-session",
  "set-session-channel-filter",
  "set-show-hidden-sessions",
  "select-background-process",
  "refresh-background-processes",
  "begin-sidebar-resize",
  "toggle-sidebar-collapsed",
  "open-settings",
]);

const deleteMode = ref(false);
const selectedSessionIds = ref(new Set());

const selectedSessions = computed(() => {
  return props.sessions.filter((session) => selectedSessionIds.value.has(sessionSelectionKey(session)));
});

watch(
  () => props.sessions.map((session) => sessionSelectionKey(session)).join("\n"),
  () => {
    const availableIds = new Set(props.sessions.map((session) => sessionSelectionKey(session)));
    selectedSessionIds.value = new Set([...selectedSessionIds.value].filter((id) => availableIds.has(id)));
    if (deleteMode.value && props.sessions.length === 0) {
      cancelDeleteMode();
    }
  },
);

function sessionSelectionKey(session) {
  return session?.externalChatId || session?.sessionId || "";
}

function beginDeleteMode() {
  if (props.sessions.length === 0) {
    return;
  }
  selectedSessionIds.value = new Set();
  deleteMode.value = true;
}

function cancelDeleteMode() {
  selectedSessionIds.value = new Set();
  deleteMode.value = false;
}

function toggleSessionSelection(session, checked) {
  const nextIds = new Set(selectedSessionIds.value);
  const key = sessionSelectionKey(session);
  if (!key) {
    return;
  }
  if (checked) {
    nextIds.add(key);
  } else {
    nextIds.delete(key);
  }
  selectedSessionIds.value = nextIds;
}

function deleteSelectedSessions() {
  if (selectedSessions.value.length === 0) {
    return;
  }
  emit("delete-sessions", selectedSessions.value);
  cancelDeleteMode();
}
</script>
