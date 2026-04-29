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
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'mcp' }"
            type="button"
            @click="$emit('select-section', 'mcp')"
          >
            <span aria-hidden="true">◇</span>
            {{ copy.settingsTitles.mcp }}
          </button>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'schedule' }"
            type="button"
            @click="$emit('select-section', 'schedule')"
          >
            <span aria-hidden="true">◷</span>
            {{ copy.settingsTitles.schedule }}
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

        <section v-show="section === 'mcp'" class="settings-page">
          <p v-if="settingsState.mcpLoading" class="settings-inline-status">{{ copy.settings.mcp.loading }}</p>
          <p v-if="settingsState.mcpError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.mcpError }}
          </p>
          <p v-if="settingsState.mcpNotice" class="settings-inline-status settings-inline-status--success">
            {{ settingsState.mcpNotice }}
          </p>

          <h3>{{ copy.settings.mcp.runtimeTitle }}</h3>
          <div class="settings-card settings-card--form">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.mcp.runtimeStatus }}</strong>
                <span>{{ mcpRuntimeStatus }}</span>
              </div>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.mcpLoading"
                @click="$emit('reload-mcp-settings')"
              >
                {{ copy.settings.mcp.reload }}
              </button>
            </div>
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.mcp.connectedTools }}</strong>
                <span>{{ mcpToolNames }}</span>
              </div>
            </div>
          </div>

          <div v-if="settingsState.mcpForm.showEditor" class="mcp-editor-screen">
            <div class="mcp-editor-screen__header">
              <div>
                <h3>{{ settingsState.mcpForm.editingId ? copy.settings.mcp.editTitle : copy.settings.mcp.addTitle }}</h3>
                <span>{{ copy.settings.mcp.simpleHint }}</span>
              </div>
              <button class="secondary-button" type="button" @click="$emit('cancel-mcp-edit')">
                {{ copy.settings.mcp.backToList }}
              </button>
            </div>

            <div class="settings-card mcp-editor">
              <div class="schedule-form-grid">
                <label class="channel-field">
                  <span>{{ copy.settings.mcp.serverId }}</span>
                  <input
                    v-model="settingsState.mcpForm.serverId"
                    type="text"
                    :disabled="Boolean(settingsState.mcpForm.editingId)"
                    spellcheck="false"
                  />
                </label>

                <label class="channel-field">
                  <span>{{ copy.settings.mcp.transport }}</span>
                  <select v-model="settingsState.mcpForm.type">
                    <option value="stdio">stdio</option>
                    <option value="sse">sse</option>
                    <option value="streamableHttp">streamableHttp</option>
                  </select>
                </label>

                <label v-if="settingsState.mcpForm.type === 'stdio'" class="channel-field channel-field--wide">
                  <span>{{ copy.settings.mcp.command }}</span>
                  <input v-model="settingsState.mcpForm.command" type="text" spellcheck="false" />
                </label>

                <label v-if="settingsState.mcpForm.type === 'stdio'" class="channel-field channel-field--wide">
                  <span>{{ copy.settings.mcp.args }}</span>
                  <textarea v-model="settingsState.mcpForm.argsText" rows="3" spellcheck="false"></textarea>
                </label>

                <label v-if="settingsState.mcpForm.type !== 'stdio'" class="channel-field channel-field--wide">
                  <span>{{ copy.settings.mcp.url }}</span>
                  <input v-model="settingsState.mcpForm.url" type="text" spellcheck="false" />
                </label>
              </div>

              <div class="mcp-editor__toolbar">
                <button class="secondary-button" type="button" @click="$emit('toggle-mcp-advanced')">
                  {{ settingsState.mcpForm.showAdvanced ? copy.settings.mcp.hideAdvanced : copy.settings.mcp.showAdvanced }}
                </button>
                <button class="secondary-button" type="button" @click="$emit('toggle-mcp-json')">
                  {{ settingsState.mcpForm.showJsonInput ? copy.settings.mcp.hideJson : copy.settings.mcp.showJson }}
                </button>
              </div>

              <div v-if="settingsState.mcpForm.showJsonInput" class="mcp-editor__json">
                <label class="channel-field channel-field--wide">
                  <span>{{ copy.settings.mcp.configJson }}</span>
                  <textarea
                    v-model="settingsState.mcpForm.jsonText"
                    rows="7"
                    spellcheck="false"
                    :placeholder="copy.settings.mcp.configJsonPlaceholder"
                  ></textarea>
                </label>
                <button class="secondary-button" type="button" @click="$emit('apply-mcp-json')">
                  {{ copy.settings.mcp.applyJson }}
                </button>
              </div>

              <div v-if="settingsState.mcpForm.showAdvanced" class="schedule-form-grid">
                <div class="mcp-editor__section-title channel-field--wide">
                  <strong>{{ copy.settings.mcp.advancedTitle }}</strong>
                  <span>{{ copy.settings.mcp.advancedHint }}</span>
                </div>

                <label class="channel-field">
                  <span>{{ copy.settings.mcp.toolTimeout }}</span>
                  <input v-model="settingsState.mcpForm.toolTimeout" type="number" min="1" step="1" />
                </label>

                <label class="channel-field">
                  <span>{{ copy.settings.mcp.enabledTools }}</span>
                  <textarea v-model="settingsState.mcpForm.enabledToolsText" rows="2" spellcheck="false"></textarea>
                </label>

                <label class="channel-field channel-field--wide">
                  <span>{{ copy.settings.mcp.env }}</span>
                  <textarea v-model="settingsState.mcpForm.envJson" rows="3" spellcheck="false" :placeholder="copy.settings.mcp.jsonPlaceholder"></textarea>
                </label>

                <label class="channel-field channel-field--wide">
                  <span>{{ copy.settings.mcp.headers }}</span>
                  <textarea v-model="settingsState.mcpForm.headersJson" rows="3" spellcheck="false" :placeholder="copy.settings.mcp.jsonPlaceholder"></textarea>
                </label>
              </div>

              <div class="mcp-editor__actions">
                <button
                  class="primary-button"
                  type="button"
                  :disabled="settingsState.mcpLoading"
                  @click="$emit('save-mcp-server')"
                >
                  {{ settingsState.mcpForm.editingId ? copy.settings.mcp.update : copy.settings.mcp.add }}
                </button>
                <button class="secondary-button" type="button" @click="$emit('cancel-mcp-edit')">
                  {{ copy.settings.mcp.cancelEdit }}
                </button>
              </div>
            </div>
          </div>

          <div v-else class="mcp-server-list-screen">
            <div class="mcp-server-list-screen__header">
              <h3>{{ copy.settings.mcp.serversTitle }}</h3>
              <button class="primary-button" type="button" @click="$emit('begin-mcp-create')">
                {{ copy.settings.mcp.openAdd }}
              </button>
            </div>
            <div class="settings-card provider-card">
              <div v-if="settingsState.mcp.servers.length === 0" class="provider-row provider-row--empty">
                <div>
                  <strong>{{ copy.settings.mcp.noServersTitle }}</strong>
                  <span>{{ copy.settings.mcp.noServersDescription }}</span>
                </div>
              </div>

              <div v-for="server in settingsState.mcp.servers" :key="server.id" class="schedule-job-row">
                <div class="schedule-job-row__main">
                  <div class="provider-row__title">
                    <strong>{{ server.name }}</strong>
                    <span class="provider-row__badge">{{ server.type || copy.settings.mcp.autoTransport }}</span>
                  </div>
                  <span>{{ server.command || server.url || copy.settings.mcp.noEndpoint }}</span>
                  <span>{{ copy.settings.mcp.toolsLabel(server.enabled_tools.join(', ')) }}</span>
                  <span v-if="server.env_configured">{{ copy.settings.mcp.envKeys(server.env_keys.join(', ')) }}</span>
                  <span v-if="server.headers_configured">{{ copy.settings.mcp.headerKeys(server.headers_keys.join(', ')) }}</span>
                </div>

                <div class="schedule-job-row__actions">
                  <button class="secondary-button" type="button" @click="$emit('edit-mcp-server', server)">
                    {{ copy.settings.mcp.edit }}
                  </button>
                  <button
                    class="secondary-button"
                    type="button"
                    :disabled="settingsState.mcpLoading"
                    @click="$emit('remove-mcp-server', server)"
                  >
                    {{ copy.settings.mcp.remove }}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section v-show="section === 'schedule'" class="settings-page">
          <p v-if="settingsState.scheduleLoading" class="settings-inline-status">{{ copy.settings.schedule.loading }}</p>
          <p v-if="settingsState.scheduleError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.scheduleError }}
          </p>
          <p v-if="settingsState.scheduleNotice" class="settings-inline-status settings-inline-status--success">
            {{ settingsState.scheduleNotice }}
          </p>

          <h3>{{ copy.settings.schedule.defaultsTitle }}</h3>
          <div class="settings-card settings-card--form">
            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.schedule.defaultTimezone.title }}</strong>
                <span>{{ copy.settings.schedule.defaultTimezone.description }}</span>
              </div>
              <select
                v-model="settingsState.scheduleForm.defaultTimezone"
                :aria-label="copy.settings.schedule.defaultTimezone.title"
                :disabled="settingsState.scheduleLoading"
                @keydown.enter.prevent="$emit('save-schedule-settings')"
              >
                <option
                  v-for="timezone in scheduleTimezoneOptions"
                  :key="timezone"
                  :value="timezone"
                >
                  {{ timezone }}
                </option>
              </select>
            </label>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.schedule.currentTitle }}</strong>
                <span>{{ settingsState.schedule.default_timezone || 'UTC' }}</span>
              </div>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.scheduleLoading"
                @click="$emit('save-schedule-settings')"
              >
                {{ copy.settings.schedule.save }}
              </button>
            </div>
          </div>

          <h3>{{ copy.settings.schedule.manageTitle }}</h3>
          <p v-if="settingsState.cronJobsError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.cronJobsError }}
          </p>
          <p v-if="settingsState.cronJobsNotice" class="settings-inline-status settings-inline-status--success">
            {{ settingsState.cronJobsNotice }}
          </p>

          <div class="settings-card schedule-editor">
            <div class="schedule-editor__header">
              <div>
                <strong>
                  {{ settingsState.cronJobForm.jobId ? copy.settings.schedule.editJobTitle : copy.settings.schedule.newJobTitle }}
                </strong>
                <span>{{ copy.settings.schedule.newJobDescription }}</span>
              </div>
              <button
                v-if="settingsState.cronJobForm.jobId"
                class="secondary-button"
                type="button"
                @click="$emit('cancel-cron-job-edit')"
              >
                {{ copy.settings.schedule.cancelEdit }}
              </button>
            </div>

            <div class="schedule-form-grid">
              <label class="channel-field">
                <span>{{ copy.settings.schedule.jobName }}</span>
                <input v-model="settingsState.cronJobForm.name" type="text" />
              </label>

              <label class="channel-field">
                <span>{{ copy.settings.schedule.jobType }}</span>
                <select v-model="settingsState.cronJobForm.mode">
                  <option value="cron">{{ copy.settings.schedule.jobTypes.cron }}</option>
                  <option value="every">{{ copy.settings.schedule.jobTypes.every }}</option>
                  <option value="at">{{ copy.settings.schedule.jobTypes.at }}</option>
                </select>
              </label>

              <label v-if="settingsState.cronJobForm.mode === 'every'" class="channel-field">
                <span>{{ copy.settings.schedule.everySeconds }}</span>
                <input v-model="settingsState.cronJobForm.everySeconds" type="number" min="1" step="1" />
              </label>

              <label v-if="settingsState.cronJobForm.mode === 'cron'" class="channel-field channel-field--wide">
                <span>{{ copy.settings.schedule.cronExpression }}</span>
                <input v-model="settingsState.cronJobForm.cronExpr" type="text" spellcheck="false" />
              </label>

              <label v-if="settingsState.cronJobForm.mode === 'cron'" class="channel-field">
                <span>{{ copy.settings.schedule.timezone }}</span>
                <select v-model="settingsState.cronJobForm.timezone">
                  <option
                    v-for="timezone in scheduleTimezoneOptions"
                    :key="timezone"
                    :value="timezone"
                  >
                    {{ timezone }}
                  </option>
                </select>
              </label>

              <label v-if="settingsState.cronJobForm.mode === 'at'" class="channel-field">
                <span>{{ copy.settings.schedule.runAt }}</span>
                <input v-model="settingsState.cronJobForm.at" type="datetime-local" />
              </label>

              <label class="channel-field channel-field--wide">
                <span>{{ copy.settings.schedule.message }}</span>
                <textarea v-model="settingsState.cronJobForm.message" rows="3" spellcheck="false"></textarea>
              </label>

              <label class="settings-row schedule-editor__deliver">
                <div>
                  <strong>{{ copy.settings.schedule.deliver.title }}</strong>
                  <span>{{ copy.settings.schedule.deliver.description }}</span>
                </div>
                <input v-model="settingsState.cronJobForm.deliver" class="switch" type="checkbox" />
              </label>

              <button
                class="primary-button schedule-editor__submit"
                type="button"
                :disabled="settingsState.cronJobsLoading"
                @click="$emit('save-cron-job')"
              >
                {{ settingsState.cronJobForm.jobId ? copy.settings.schedule.updateJob : copy.settings.schedule.createJob }}
              </button>
            </div>
          </div>

          <h3>{{ copy.settings.schedule.jobsTitle }}</h3>
          <p v-if="settingsState.cronJobsLoading" class="settings-inline-status">{{ copy.settings.schedule.jobsLoading }}</p>
          <div class="settings-card provider-card">
            <div
              v-if="!settingsState.cronJobsLoading && settingsState.cronJobs.length === 0"
              class="provider-row provider-row--empty"
            >
              <div>
                <strong>{{ copy.settings.schedule.noJobsTitle }}</strong>
                <span>{{ copy.settings.schedule.noJobsDescription }}</span>
              </div>
            </div>

            <div v-for="job in settingsState.cronJobs" :key="job.id" class="schedule-job-row">
              <div class="schedule-job-row__main">
                <div class="provider-row__title">
                  <strong>{{ job.name }}</strong>
                  <span class="provider-row__badge">
                    {{ job.enabled ? copy.settings.schedule.enabled : copy.settings.schedule.paused }}
                  </span>
                </div>
                <span>{{ job.schedule.display }}</span>
                <span v-if="job.session_id">{{ copy.settings.schedule.sessionLabel(job.session_id) }}</span>
                <span v-if="job.state.next_run_display">{{ copy.settings.schedule.nextRun(job.state.next_run_display) }}</span>
                <p>{{ job.payload.message }}</p>
              </div>

              <div class="schedule-job-row__actions">
                <button class="secondary-button" type="button" @click="$emit('edit-cron-job', job)">
                  {{ copy.settings.schedule.edit }}
                </button>
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.cronJobsLoading"
                  @click="$emit('cron-job-action', job, job.enabled ? 'pause' : 'enable')"
                >
                  {{ job.enabled ? copy.settings.schedule.pause : copy.settings.schedule.enable }}
                </button>
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.cronJobsLoading"
                  @click="$emit('cron-job-action', job, 'run')"
                >
                  {{ copy.settings.schedule.runNow }}
                </button>
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.cronJobsLoading"
                  @click="$emit('cron-job-action', job, 'remove')"
                >
                  {{ copy.settings.schedule.remove }}
                </button>
              </div>
            </div>
          </div>

          <h3>{{ copy.settings.schedule.usageTitle }}</h3>
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.schedule.usageCron.title }}</strong>
                <span>{{ copy.settings.schedule.usageCron.description }}</span>
              </div>
            </div>
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.schedule.usageExisting.title }}</strong>
                <span>{{ copy.settings.schedule.usageExisting.description }}</span>
              </div>
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

const scheduleTimezoneOptions = computed(() => {
  const configured = Array.isArray(props.settingsState.schedule.common_timezones)
    ? props.settingsState.schedule.common_timezones
    : [];
  const options = configured.map((timezone) => String(timezone || "").trim()).filter(Boolean);
  const current = String(
    props.settingsState.scheduleForm.defaultTimezone || props.settingsState.schedule.default_timezone || "UTC",
  ).trim() || "UTC";
  const uniqueOptions = Array.from(new Set(options.length ? options : ["UTC"]));
  if (!uniqueOptions.includes(current)) {
    uniqueOptions.unshift(current);
  }
  return uniqueOptions;
});

const mcpRuntimeStatus = computed(() => {
  const runtime = props.settingsState.mcp.runtime || {};
  if (runtime.connecting) {
    return props.copy.settings.mcp.runtimeConnecting;
  }
  if (runtime.connected) {
    return props.copy.settings.mcp.runtimeConnected;
  }
  if (runtime.connect_failures) {
    return props.copy.settings.mcp.runtimeFailed(runtime.connect_failures);
  }
  return props.copy.settings.mcp.runtimeDisconnected;
});

const mcpToolNames = computed(() => {
  const toolNames = props.settingsState.mcp.runtime?.tool_names || [];
  return toolNames.length ? toolNames.join(", ") : props.copy.settings.mcp.noTools;
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
  "begin-mcp-create",
  "save-mcp-server",
  "edit-mcp-server",
  "cancel-mcp-edit",
  "remove-mcp-server",
  "reload-mcp-settings",
  "toggle-mcp-advanced",
  "toggle-mcp-json",
  "apply-mcp-json",
  "save-schedule-settings",
  "save-cron-job",
  "edit-cron-job",
  "cancel-cron-job-edit",
  "cron-job-action",
]);
</script>
