<template>
  <div v-if="open" class="settings-modal">
    <button
      class="settings-modal__backdrop"
      type="button"
      :aria-label="copy.settings.closeAria"
      @click="$emit('close')"
    ></button>

    <section class="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
      <aside class="settings-nav" aria-label="Settings sections">
        <div class="settings-nav__group">
          <p>{{ copy.settings.desktop }}</p>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'general' }"
            type="button"
            @click="$emit('select-section', 'general')"
          >
            <span aria-hidden="true">⌘</span>
            {{ copy.settingsTitles.general }}
          </button>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'shortcuts' }"
            type="button"
            @click="$emit('select-section', 'shortcuts')"
          >
            <span aria-hidden="true">⌗</span>
            {{ copy.settingsTitles.shortcuts }}
          </button>
        </div>

        <div class="settings-nav__group">
          <p>{{ copy.settings.server }}</p>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'providers' }"
            type="button"
            @click="$emit('select-section', 'providers')"
          >
            <span aria-hidden="true">⚙</span>
            {{ copy.settingsTitles.providers }}
          </button>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'models' }"
            type="button"
            @click="$emit('select-section', 'models')"
          >
            <span aria-hidden="true">✦</span>
            {{ copy.settingsTitles.models }}
          </button>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'channels' }"
            type="button"
            @click="$emit('select-section', 'channels')"
          >
            <span aria-hidden="true">☷</span>
            {{ copy.settingsTitles.channels }}
          </button>
        </div>

        <div class="settings-nav__footer">
          <strong>OpenSprite Web</strong>
          <span>{{ copy.settings.version }}</span>
        </div>
      </aside>

      <div class="settings-content">
        <header class="settings-content__header">
          <h2 id="settingsTitle">{{ title }}</h2>
          <button class="settings-panel__close" type="button" :aria-label="copy.settings.closeAria" @click="$emit('close')">
            {{ copy.settings.close }}
          </button>
        </header>

        <section v-show="section === 'general'" class="settings-page">
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.general.language.title }}</strong>
                <span>{{ copy.settings.general.language.description }}</span>
              </div>
              <select v-model="form.language" :aria-label="copy.settings.general.language.title">
                <option value="zh-TW">{{ copy.settings.general.language.options.zhTW }}</option>
                <option value="en">{{ copy.settings.general.language.options.en }}</option>
              </select>
            </div>

            <label class="settings-row">
              <div>
                <strong>{{ copy.settings.general.runTimeline.title }}</strong>
                <span>{{ copy.settings.general.runTimeline.description }}</span>
              </div>
              <input v-model="form.showRunTimeline" class="switch" type="checkbox" />
            </label>

            <label class="settings-row">
              <div>
                <strong>{{ copy.settings.general.runTrace.title }}</strong>
                <span>{{ copy.settings.general.runTrace.description }}</span>
              </div>
              <input v-model="form.showRunTrace" class="switch" type="checkbox" />
            </label>
          </div>

          <h3>{{ copy.settings.general.connectionTitle }}</h3>
          <div class="settings-card settings-card--form">
            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.general.wsUrl.title }}</strong>
                <span>{{ copy.settings.general.wsUrl.description }}</span>
              </div>
              <input v-model="form.wsUrl" type="text" spellcheck="false" @change="$emit('save-connection-settings')" />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.general.displayName.title }}</strong>
                <span>{{ copy.settings.general.displayName.description }}</span>
              </div>
              <input v-model="form.displayName" type="text" maxlength="60" @change="$emit('save-connection-settings')" />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.general.externalChatId.title }}</strong>
                <span>{{ copy.settings.general.externalChatId.description }}</span>
              </div>
              <input v-model="form.externalChatId" type="text" spellcheck="false" @change="$emit('save-connection-settings')" />
            </label>

            <label class="settings-row">
              <div>
                <strong>{{ copy.settings.general.gateway.title }}</strong>
                <span>{{ connectionSwitchLabel }}</span>
              </div>
              <input
                class="switch"
                type="checkbox"
                :checked="connectionSwitchChecked"
                :disabled="connectionState === 'connecting'"
                @change="$emit('toggle-connection', $event.target.checked)"
              />
            </label>
          </div>

          <h3>{{ copy.settings.general.appearanceTitle }}</h3>
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.general.colorScheme.title }}</strong>
                <span>{{ copy.settings.general.colorScheme.description }}</span>
              </div>
              <select v-model="form.colorScheme" :aria-label="copy.settings.general.colorScheme.title">
                <option value="system">{{ copy.settings.general.colorScheme.options.system }}</option>
                <option value="light">{{ copy.settings.general.colorScheme.options.light }}</option>
                <option value="dark">{{ copy.settings.general.colorScheme.options.dark }}</option>
              </select>
            </div>
          </div>
        </section>

        <section v-show="section === 'shortcuts'" class="settings-page">
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.shortcuts.openSettings }}</strong>
                <span>{{ copy.settings.shortcuts.openSettingsDescription }}</span>
              </div>
              <div class="shortcut-keys"><kbd>Ctrl</kbd><kbd>,</kbd></div>
            </div>
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.shortcuts.sendMessage }}</strong>
                <span>{{ copy.settings.shortcuts.sendMessageDescription }}</span>
              </div>
              <div class="shortcut-keys"><kbd>Enter</kbd></div>
            </div>
          </div>
        </section>

        <section v-show="section === 'channels'" class="settings-page">
          <p v-if="settingsState.channelsLoading" class="settings-inline-status">{{ copy.settings.channels.loading }}</p>
          <p v-if="settingsState.channelsError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.channelsError }}
          </p>
          <p v-if="settingsState.channelsNotice" class="settings-inline-status settings-inline-status--success">
            {{ settingsState.channelsNotice }}
          </p>

          <h3>{{ copy.settings.channels.connectedTitle }}</h3>
          <div class="settings-card provider-card">
            <div v-if="settingsState.channels.connected.length === 0" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.channels.noConnectedTitle }}</strong>
                <span>{{ copy.settings.channels.noConnectedDescription }}</span>
              </div>
            </div>

            <div v-for="channel in settingsState.channels.connected" :key="channel.id" class="provider-row">
              <div class="provider-row__main">
                <span class="provider-row__mark" aria-hidden="true">{{ channel.name.slice(0, 2) }}</span>
                <div>
                  <div class="provider-row__title">
                    <strong>{{ channel.name }}</strong>
                    <span class="provider-row__badge">{{ copy.settings.channels.connectedBadge }}</span>
                    <span v-if="channel.enabled" class="provider-row__badge">{{ copy.settings.channels.enabledBadge }}</span>
                  </div>
                  <span>{{ channel.description }}</span>
                </div>
              </div>
              <button
                class="provider-row__action"
                type="button"
                :disabled="settingsState.channelsLoading"
                @click="$emit('disconnect-channel', channel)"
              >
                {{ copy.settings.channels.disconnect }}
              </button>
            </div>
          </div>

          <h3>{{ copy.settings.channels.availableTitle }}</h3>
          <div class="settings-card provider-card">
            <div v-if="settingsState.channels.available.length === 0" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.channels.noAvailableTitle }}</strong>
                <span>{{ copy.settings.channels.noAvailableDescription }}</span>
              </div>
            </div>

            <div v-for="channel in settingsState.channels.available" :key="channel.id" class="provider-row provider-row--stacked">
              <div class="provider-row__content">
                <div class="provider-row__main">
                  <span class="provider-row__mark" aria-hidden="true">{{ channel.name.slice(0, 2) }}</span>
                  <div>
                    <div class="provider-row__title">
                      <strong>{{ channel.name }}</strong>
                      <span class="provider-row__badge">{{ copy.settings.channels.builtInBadge }}</span>
                    </div>
                    <span>{{ channel.description }}</span>
                  </div>
                </div>
                <button
                  class="provider-row__action"
                  type="button"
                  :disabled="settingsState.channelsLoading"
                  @click="$emit('begin-channel-connect', channel)"
                >
                  {{ copy.settings.channels.add }}
                </button>
              </div>
            </div>
          </div>
        </section>

        <section v-show="section === 'providers'" class="settings-page">
          <p v-if="settingsState.providersLoading" class="settings-inline-status">{{ copy.settings.providers.loading }}</p>
          <p v-if="settingsState.providersError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.providersError }}
          </p>
          <p v-if="settingsState.providersNotice" class="settings-inline-status settings-inline-status--success">
            {{ settingsState.providersNotice }}
          </p>

          <h3>{{ copy.settings.providers.connectedTitle }}</h3>
          <div class="settings-card provider-card">
            <div v-if="settingsState.providers.connected.length === 0" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.providers.noConnectedTitle }}</strong>
                <span>{{ copy.settings.providers.noConnectedDescription }}</span>
              </div>
            </div>

            <div v-for="provider in settingsState.providers.connected" :key="provider.id" class="provider-row">
              <div class="provider-row__main">
                <span class="provider-row__mark" aria-hidden="true">{{ provider.name.slice(0, 2) }}</span>
                <div>
                  <div class="provider-row__title">
                    <strong>{{ provider.name }}</strong>
                    <span v-if="provider.is_default" class="provider-row__badge">{{ copy.settings.providers.currentBadge }}</span>
                  </div>
                  <span>{{ provider.base_url }}</span>
                </div>
              </div>
              <button
                class="provider-row__action"
                type="button"
                :disabled="settingsState.providersLoading"
                @click="$emit('disconnect-provider', provider)"
              >
                {{ copy.settings.providers.disconnect }}
              </button>
            </div>
          </div>

          <h3>{{ copy.settings.providers.popularTitle }}</h3>
          <div class="settings-card provider-card">
            <div v-if="settingsState.providers.available.length === 0" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.providers.noAvailableTitle }}</strong>
                <span>{{ copy.settings.providers.noAvailableDescription }}</span>
              </div>
            </div>

            <div v-for="provider in settingsState.providers.available" :key="provider.id" class="provider-row provider-row--stacked">
              <div class="provider-row__content">
                <div class="provider-row__main">
                  <span class="provider-row__mark" aria-hidden="true">{{ provider.name.slice(0, 2) }}</span>
                  <div>
                    <div class="provider-row__title">
                      <strong>{{ provider.name }}</strong>
                      <span class="provider-row__badge">{{ copy.settings.providers.builtInBadge }}</span>
                    </div>
                    <span>{{ provider.default_base_url }}</span>
                  </div>
                </div>
                <button
                  class="provider-row__action"
                  type="button"
                  :disabled="settingsState.providersLoading"
                  @click="$emit('begin-provider-connect', provider)"
                >
                  {{ copy.settings.providers.connect }}
                </button>
              </div>

            </div>
          </div>
        </section>

        <section v-show="section === 'models'" class="settings-page">
          <p v-if="settingsState.modelsLoading" class="settings-inline-status">{{ copy.settings.models.loading }}</p>
          <p v-if="settingsState.modelsError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.modelsError }}
          </p>
          <p v-if="settingsState.modelsNotice" class="settings-inline-status settings-inline-status--success">
            {{ settingsState.modelsNotice }}
          </p>

          <div v-if="settingsState.models.providers.length === 0" class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.models.noProvidersTitle }}</strong>
                <span>{{ copy.settings.models.noProvidersDescription }}</span>
              </div>
              <span class="settings-muted">{{ copy.settings.models.noProvidersBadge }}</span>
            </div>
          </div>

          <div
            v-for="provider in settingsState.models.providers"
            :key="provider.id"
            class="settings-card model-provider-card"
          >
            <div class="model-provider-card__header">
              <div class="provider-row__main">
                <span class="provider-row__mark" aria-hidden="true">{{ provider.name.slice(0, 2) }}</span>
                <div>
                  <div class="provider-row__title">
                    <strong>{{ provider.name }}</strong>
                    <span v-if="provider.is_default" class="provider-row__badge">{{ copy.settings.models.currentBadge }}</span>
                  </div>
                  <span>{{ provider.selected_model || copy.settings.models.noModel }}</span>
                </div>
              </div>
            </div>

            <div class="model-grid">
              <button
                v-for="model in provider.models"
                :key="`${provider.id}:${model}`"
                class="model-option"
                :class="{ 'model-option--active': provider.is_default && provider.selected_model === model }"
                type="button"
                :disabled="settingsState.modelsLoading"
                @click="$emit('select-model', provider.id, model)"
              >
                <strong>{{ model }}</strong>
                <span>{{ provider.is_default && provider.selected_model === model ? copy.settings.models.active : copy.settings.models.select }}</span>
              </button>
            </div>

            <div class="custom-model-row">
              <label>
                <span>{{ copy.settings.models.customModel }}</span>
                <input
                  v-model="settingsState.customModels[provider.id]"
                  type="text"
                  :placeholder="copy.settings.models.customPlaceholder"
                  spellcheck="false"
                />
              </label>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.modelsLoading"
                @click="$emit('select-model', provider.id, settingsState.customModels[provider.id])"
              >
                {{ copy.settings.models.useCustom }}
              </button>
            </div>
          </div>
        </section>
      </div>

      <div v-if="selectedConnectProvider" class="provider-connect-dialog" role="dialog" aria-modal="true">
        <header class="provider-connect-dialog__top">
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            :aria-label="copy.settings.providers.backAria"
            @click="$emit('cancel-provider-connect')"
          >
            ←
          </button>
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            :aria-label="copy.settings.providers.closeAria"
            @click="$emit('cancel-provider-connect')"
          >
            ×
          </button>
        </header>

        <form class="provider-connect-dialog__body" @submit.prevent="$emit('save-provider-connection')">
          <div class="provider-connect-dialog__title">
            <span class="provider-row__mark" aria-hidden="true">{{ selectedConnectProvider.name.slice(0, 2) }}</span>
            <h3>{{ copy.settings.providers.dialogTitle(selectedConnectProvider.name) }}</h3>
          </div>

          <p>
            {{ copy.settings.providers.dialogDescription(selectedConnectProvider.name) }}
          </p>

          <label class="provider-connect-field">
            <span>{{ copy.settings.providers.apiKeyLabel(selectedConnectProvider.name) }}</span>
            <input
              v-model="settingsState.connectForm.apiKey"
              type="password"
              placeholder="API key"
              autocomplete="off"
            />
          </label>

          <button
            class="provider-connect-dialog__advanced"
            type="button"
            @click="settingsState.connectForm.showAdvanced = !settingsState.connectForm.showAdvanced"
          >
            {{ settingsState.connectForm.showAdvanced ? copy.settings.providers.advancedHide : copy.settings.providers.advancedShow }}
          </button>

          <label v-if="settingsState.connectForm.showAdvanced" class="provider-connect-field">
            <span>Base URL</span>
            <input v-model="settingsState.connectForm.baseUrl" type="text" spellcheck="false" />
          </label>

          <button class="primary-button provider-connect-dialog__submit" type="submit">
            {{ copy.settings.providers.submit }}
          </button>
        </form>
      </div>

      <div v-if="selectedConnectChannel" class="provider-connect-dialog" role="dialog" aria-modal="true">
        <header class="provider-connect-dialog__top">
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            :aria-label="copy.settings.channels.backAria"
            @click="$emit('cancel-channel-connect')"
          >
            ←
          </button>
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            :aria-label="copy.settings.channels.closeAria"
            @click="$emit('cancel-channel-connect')"
          >
            ×
          </button>
        </header>

        <form class="provider-connect-dialog__body" @submit.prevent="$emit('save-channel-connection')">
          <div class="provider-connect-dialog__title">
            <span class="provider-row__mark" aria-hidden="true">{{ selectedConnectChannel.name.slice(0, 2) }}</span>
            <h3>{{ copy.settings.channels.dialogTitle(selectedConnectChannel.name) }}</h3>
          </div>

          <p>{{ copy.settings.channels.dialogDescription(selectedConnectChannel.name) }}</p>

          <label class="provider-connect-field">
            <span>{{ copy.settings.channels.nameLabel }}</span>
            <input
              v-model="settingsState.channelConnectForm.name"
              type="text"
              :placeholder="copy.settings.channels.namePlaceholder"
              autocomplete="off"
            />
          </label>

          <label class="provider-connect-field">
            <span>{{ copy.settings.channels.tokenLabel(selectedConnectChannel.name) }}</span>
            <input
              v-model="settingsState.channelConnectForm.token"
              type="password"
              placeholder="Token"
              autocomplete="off"
            />
          </label>

          <button class="primary-button provider-connect-dialog__submit" type="submit">
            {{ copy.settings.channels.submit }}
          </button>
        </form>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  copy: {
    type: Object,
    required: true,
  },
  open: {
    type: Boolean,
    required: true,
  },
  section: {
    type: String,
    required: true,
  },
  title: {
    type: String,
    required: true,
  },
  form: {
    type: Object,
    required: true,
  },
  settingsState: {
    type: Object,
    required: true,
  },
  connectionState: {
    type: String,
    required: true,
  },
});

const selectedConnectProvider = computed(() => {
  const providerId = props.settingsState.connectForm.providerId;
  if (!providerId) {
    return null;
  }
  return (
    props.settingsState.providers.available.find((provider) => provider.id === providerId) ||
    props.settingsState.providers.connected.find((provider) => provider.id === providerId) ||
    null
  );
});

const selectedConnectChannel = computed(() => {
  const channelType = props.settingsState.channelConnectForm.type;
  if (!channelType) {
    return null;
  }
  return (
    props.settingsState.channels.available.find((channel) => (channel.type || channel.id) === channelType) ||
    props.settingsState.channels.connected.find((channel) => (channel.type || channel.id) === channelType) ||
    null
  );
});

const connectionSwitchChecked = computed(
  () => props.connectionState === "connected" || props.connectionState === "connecting",
);

const connectionSwitchLabel = computed(() => {
  if (props.connectionState === "connecting") {
    return props.copy.settings.general.gateway.connecting;
  }
  if (props.connectionState === "connected") {
    return props.copy.settings.general.gateway.connected;
  }
  return props.copy.settings.general.gateway.disconnected;
});

defineEmits([
  "close",
  "select-section",
  "save-connection-settings",
  "toggle-connection",
  "begin-channel-connect",
  "cancel-channel-connect",
  "save-channel-connection",
  "disconnect-channel",
  "begin-provider-connect",
  "cancel-provider-connect",
  "save-provider-connection",
  "disconnect-provider",
  "select-model",
]);
</script>
