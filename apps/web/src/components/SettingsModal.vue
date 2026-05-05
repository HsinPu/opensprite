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
          <p>{{ copy.settings.web }}</p>
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
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'curator' }"
            type="button"
            @click="$emit('select-section', 'curator')"
          >
            <span aria-hidden="true">◌</span>
            {{ copy.settingsTitles.curator }}
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
                <strong>{{ copy.settings.general.runSummary.title }}</strong>
                <span>{{ copy.settings.general.runSummary.description }}</span>
              </div>
              <input v-model="form.showRunSummary" class="switch" type="checkbox" />
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

          <h3>{{ copy.settings.general.update.title }}</h3>
          <p v-if="settingsState.updateNotice" class="settings-inline-status">{{ settingsState.updateNotice }}</p>
          <p v-if="settingsState.updateError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.updateError }}
          </p>
          <div class="settings-card">
            <div class="settings-row settings-row--update">
              <div>
                <strong>{{ updateStatusLabel }}</strong>
                <span>{{ updateStatusDescription }}</span>
              </div>
              <div class="settings-row__actions">
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.updateLoading"
                  @click="$emit('check-update')"
                >
                  {{ copy.settings.general.update.check }}
                </button>
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.updateLoading || !settingsState.updateStatus.supported || settingsState.updateStatus.dirty"
                  @click="$emit('run-update')"
                >
                  {{ copy.settings.general.update.apply }}
                </button>
              </div>
            </div>
          </div>

        </section>

        <section v-show="section === 'curator'" class="settings-page">
          <CuratorSettingsPage
            :copy="copy"
            :state="curatorState"
            :status="curatorStatus"
            @refresh-curator="$emit('refresh-curator')"
            @run-curator-action="$emit('run-curator-action', $event)"
          />
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

          <h3>{{ copy.settings.providers.codexAuth.title }}</h3>
          <p v-if="settingsState.codexAuthNotice" class="settings-inline-status">{{ settingsState.codexAuthNotice }}</p>
          <p v-if="settingsState.codexAuthError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.codexAuthError }}
          </p>
          <div class="settings-card provider-card">
            <div class="provider-row provider-row--stacked codex-auth-row">
              <div class="provider-row__content">
                <div class="provider-row__main">
                  <span class="provider-row__mark" aria-hidden="true">Cx</span>
                  <div>
                    <div class="provider-row__title">
                      <strong>{{ copy.settings.providers.codexAuth.name }}</strong>
                      <span class="provider-row__badge">{{ codexAuthStatusLabel }}</span>
                    </div>
                    <span>{{ codexAuthDescription }}</span>
                  </div>
                </div>
                <div class="provider-row__actions">
                  <button
                    class="provider-row__action"
                    type="button"
                    :disabled="settingsState.codexAuthLoading"
                    @click="$emit('refresh-codex-auth')"
                  >
                    {{ copy.settings.providers.codexAuth.refresh }}
                  </button>
                  <button
                    class="provider-row__action"
                    type="button"
                    :disabled="settingsState.codexAuthLoading"
                    @click="$emit('start-codex-auth-login')"
                  >
                    {{ copy.settings.providers.codexAuth.login }}
                  </button>
                  <button
                    class="provider-row__action"
                    type="button"
                    :disabled="settingsState.codexAuthLoading || !settingsState.codexAuth.configured"
                    @click="$emit('logout-codex-auth')"
                  >
                    {{ copy.settings.providers.codexAuth.logout }}
                  </button>
                </div>
              </div>
              <div v-if="settingsState.codexAuth.command" class="codex-auth-command">
                <span>{{ copy.settings.providers.codexAuth.commandLabel }}</span>
                <code>{{ settingsState.codexAuth.command }}</code>
              </div>
            </div>
          </div>

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
                    <span v-if="provider.preset_name && provider.preset_name !== provider.name" class="provider-row__badge">{{ provider.preset_name }}</span>
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
                      <span v-if="provider.connected_count" class="provider-row__badge">{{ copy.settings.providers.connectedCount(provider.connected_count) }}</span>
                    </div>
                    <span>{{ provider.default_base_url }}</span>
                  </div>
                </div>
                <button
                  class="provider-row__action"
                  type="button"
                  :disabled="settingsState.providersLoading"
                  @click="provider.requires_api_key === false ? $emit('connect-codex-provider', provider) : $emit('begin-provider-connect', provider)"
                >
                  {{ provider.requires_api_key === false ? copy.settings.providers.connectOAuth : copy.settings.providers.connect }}
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
          <p v-if="settingsState.mediaError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.mediaError }}
          </p>

          <h3>{{ copy.settings.models.textTitle }}</h3>
          <div v-if="settingsState.models.providers.length === 0" class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.models.noProvidersTitle }}</strong>
                <span>{{ copy.settings.models.noProvidersDescription }}</span>
              </div>
              <span class="settings-muted">{{ copy.settings.models.noProvidersBadge }}</span>
            </div>
          </div>

          <div v-if="selectedTextProvider" class="settings-card model-provider-card">
            <div class="model-provider-card__header">
              <div class="provider-row__main">
                <span class="provider-row__mark" aria-hidden="true">{{ selectedTextProvider.name.slice(0, 2) }}</span>
                <div>
                  <div class="provider-row__title">
                    <strong>{{ selectedTextProvider.name }}</strong>
                    <span v-if="selectedTextProvider.is_default" class="provider-row__badge">{{ copy.settings.models.currentBadge }}</span>
                  </div>
                  <span>{{ selectedTextProvider.selected_model || copy.settings.models.noModel }}</span>
                </div>
              </div>
            </div>

            <div class="model-select-row">
              <label>
                <span>{{ copy.settings.models.providerChoice }}</span>
                <select
                  v-model="settingsState.selectedTextProviderId"
                  :disabled="settingsState.modelsLoading"
                >
                  <option v-for="provider in settingsState.models.providers" :key="provider.id" :value="provider.id">
                    {{ provider.name }}{{ provider.is_default ? ` (${copy.settings.models.active})` : '' }}
                  </option>
                </select>
              </label>
              <label>
                <span>{{ copy.settings.models.modelChoice }}</span>
                <select v-model="settingsState.modelSelections[selectedTextProvider.id]" :disabled="settingsState.modelsLoading">
                  <option v-for="model in textProviderModels" :key="`${selectedTextProvider.id}:${model}`" :value="model">
                    {{ model }}{{ selectedTextProvider.is_default && selectedTextProvider.selected_model === model ? ` (${copy.settings.models.active})` : '' }}
                  </option>
                </select>
              </label>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.modelsLoading || !settingsState.modelSelections[selectedTextProvider.id]"
                @click="$emit('select-model', selectedTextProvider.id, settingsState.modelSelections[selectedTextProvider.id])"
              >
                {{ copy.settings.models.select }}
              </button>
            </div>

            <div class="custom-model-row">
              <label>
                <span>{{ copy.settings.models.customModel }}</span>
                <input
                  v-model="settingsState.customModels[selectedTextProvider.id]"
                  type="text"
                  :placeholder="copy.settings.models.customPlaceholder"
                  spellcheck="false"
                />
              </label>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.modelsLoading"
                @click="$emit('select-model', selectedTextProvider.id, settingsState.customModels[selectedTextProvider.id])"
              >
                {{ copy.settings.models.useCustom }}
              </button>
            </div>

            <div v-if="showOpenRouterOptions" class="openrouter-options">
              <div class="openrouter-options__header">
                <div>
                  <strong>{{ copy.settings.models.openRouter.title }}</strong>
                  <span>{{ copy.settings.models.openRouter.description }}</span>
                </div>
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.modelsLoading"
                  @click="$emit('apply-openrouter-recommended-options', selectedTextProvider.id, selectedTextModel)"
                >
                  {{ copy.settings.models.openRouter.applyRecommended }}
                </button>
              </div>

              <div class="openrouter-capabilities">
                <span v-for="capability in selectedTextCapabilityBadges" :key="capability" class="provider-row__badge">
                  {{ capability }}
                </span>
                <span class="settings-muted">
                  {{ copy.settings.models.openRouter.recommendedSummary(selectedTextRecommendedOptions) }}
                </span>
              </div>

              <div class="openrouter-options__grid">
                <label class="openrouter-option-row openrouter-option-row--switch">
                  <div>
                    <strong>{{ copy.settings.models.openRouter.reasoningEnabled }}</strong>
                    <span>{{ copy.settings.models.openRouter.reasoningEnabledDescription }}</span>
                  </div>
                  <input
                    v-model="settingsState.openRouterOptions[selectedTextProvider.id].reasoningEnabled"
                    class="switch"
                    type="checkbox"
                  />
                </label>
                <label class="openrouter-option-field">
                  <span>{{ copy.settings.models.openRouter.reasoningEffort }}</span>
                  <select v-model="settingsState.openRouterOptions[selectedTextProvider.id].reasoningEffort">
                    <option value="">{{ copy.settings.models.openRouter.none }}</option>
                    <option value="minimal">minimal</option>
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                    <option value="xhigh">xhigh</option>
                  </select>
                </label>
                <label class="openrouter-option-field">
                  <span>{{ copy.settings.models.openRouter.reasoningMaxTokens }}</span>
                  <input
                    v-model="settingsState.openRouterOptions[selectedTextProvider.id].reasoningMaxTokens"
                    type="number"
                    min="1"
                    :placeholder="copy.settings.models.openRouter.none"
                  />
                </label>
                <label class="openrouter-option-row openrouter-option-row--switch">
                  <div>
                    <strong>{{ copy.settings.models.openRouter.reasoningExclude }}</strong>
                    <span>{{ copy.settings.models.openRouter.reasoningExcludeDescription }}</span>
                  </div>
                  <input
                    v-model="settingsState.openRouterOptions[selectedTextProvider.id].reasoningExclude"
                    class="switch"
                    type="checkbox"
                  />
                </label>
                <label class="openrouter-option-field">
                  <span>{{ copy.settings.models.openRouter.providerSort }}</span>
                  <select v-model="settingsState.openRouterOptions[selectedTextProvider.id].providerSort">
                    <option value="">{{ copy.settings.models.openRouter.none }}</option>
                    <option value="price">price</option>
                    <option value="throughput">throughput</option>
                    <option value="latency">latency</option>
                  </select>
                </label>
                <label class="openrouter-option-row openrouter-option-row--switch">
                  <div>
                    <strong>{{ copy.settings.models.openRouter.requireParameters }}</strong>
                    <span>{{ copy.settings.models.openRouter.requireParametersDescription }}</span>
                  </div>
                  <input
                    v-model="settingsState.openRouterOptions[selectedTextProvider.id].requireParameters"
                    class="switch"
                    type="checkbox"
                  />
                </label>
              </div>

              <button
                class="secondary-button openrouter-options__save"
                type="button"
                :disabled="settingsState.modelsLoading"
                @click="$emit('save-openrouter-options', selectedTextProvider.id)"
              >
                {{ copy.settings.models.openRouter.save }}
              </button>
            </div>
          </div>

          <h3>{{ copy.settings.models.mediaTitle }}</h3>
          <div v-if="settingsState.media.providers.length === 0" class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.models.noProvidersTitle }}</strong>
                <span>{{ copy.settings.models.mediaNoProvidersDescription }}</span>
              </div>
              <span class="settings-muted">{{ copy.settings.models.noProvidersBadge }}</span>
            </div>
          </div>

          <div
            v-for="category in mediaModelCategories"
            :key="category.key"
            class="settings-card model-provider-card"
          >
            <div class="model-provider-card__header">
              <div class="provider-row__main">
                <span class="provider-row__mark" aria-hidden="true">{{ category.mark }}</span>
                <div>
                  <div class="provider-row__title">
                    <strong>{{ category.title }}</strong>
                    <span v-if="settingsState.media.sections[category.key]?.enabled" class="provider-row__badge">{{ copy.settings.models.enabledBadge }}</span>
                  </div>
                  <span>{{ settingsState.media.sections[category.key]?.model || copy.settings.models.noModel }}</span>
                </div>
              </div>
            </div>

            <label class="settings-row">
              <div>
                <strong>{{ copy.settings.models.enableMediaModel }}</strong>
                <span>{{ category.description }}</span>
              </div>
              <input
                v-model="settingsState.mediaSelections[category.key].enabled"
                class="switch"
                type="checkbox"
                @change="syncMediaSelection(category.key)"
              />
            </label>

            <div class="model-select-row">
              <label v-if="settingsState.mediaSelections[category.key].enabled">
                <span>{{ copy.settings.models.providerChoice }}</span>
                <select
                  v-model="settingsState.mediaSelections[category.key].providerId"
                  :disabled="settingsState.mediaLoading"
                  @change="syncMediaSelection(category.key)"
                >
                  <option v-for="provider in settingsState.media.providers" :key="`${category.key}:${provider.id}`" :value="provider.id">
                    {{ provider.name }}
                  </option>
                </select>
              </label>
              <label v-if="settingsState.mediaSelections[category.key].enabled">
                <span>{{ copy.settings.models.modelChoice }}</span>
                <select v-model="settingsState.mediaSelections[category.key].model" :disabled="settingsState.mediaLoading">
                  <option v-for="model in mediaProviderModels(category.key)" :key="`${category.key}:${model}`" :value="model">
                    {{ model }}
                  </option>
                </select>
              </label>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.mediaLoading || (settingsState.mediaSelections[category.key].enabled && !settingsState.mediaSelections[category.key].providerId)"
                @click="$emit('save-media-model', category.key)"
              >
                {{ copy.settings.models.saveMediaModel }}
              </button>
            </div>

            <div v-if="settingsState.mediaSelections[category.key].enabled" class="custom-model-row">
              <label>
                <span>{{ copy.settings.models.customModel }}</span>
                <input
                  v-model="settingsState.mediaCustomModels[category.key]"
                  type="text"
                  :placeholder="copy.settings.models.customPlaceholder"
                  :disabled="settingsState.mediaLoading"
                  spellcheck="false"
                />
              </label>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.mediaLoading"
                @click="$emit('save-media-model', category.key, settingsState.mediaCustomModels[category.key])"
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
                <span v-if="mcpToolGroups.length === 0">{{ copy.settings.mcp.noTools }}</span>
              </div>
            </div>

            <div v-if="mcpToolGroups.length" class="mcp-tool-groups">
              <div v-for="group in mcpToolGroups" :key="group.serverId" class="mcp-tool-group">
                <button class="mcp-tool-group__header" type="button" @click="$emit('toggle-mcp-tool-group', group.serverId)">
                  <span aria-hidden="true">{{ group.expanded ? '▾' : '▸' }}</span>
                  <strong>{{ group.serverName }}</strong>
                  <small>{{ copy.settings.mcp.toolCount(group.tools.length) }}</small>
                </button>
                <div v-if="group.expanded" class="mcp-tool-group__tools">
                  <span v-for="tool in group.tools" :key="tool.fullName" class="mcp-tool-chip">
                    {{ tool.name }}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div class="mcp-server-list-screen">
            <div class="mcp-server-list-screen__header">
              <h3>{{ copy.settings.mcp.serversTitle }}</h3>
              <button class="provider-row__action" type="button" @click="$emit('begin-mcp-create')">
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

          <div class="schedule-list-screen__header">
            <h3>{{ copy.settings.schedule.manageTitle }}</h3>
            <button class="provider-row__action" type="button" @click="$emit('begin-cron-job-create')">
              {{ copy.settings.schedule.openAdd }}
            </button>
          </div>
          <p v-if="settingsState.cronJobsError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.cronJobsError }}
          </p>

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
            <span>{{ copy.settings.providers.nameLabel }}</span>
            <input
              v-model="settingsState.connectForm.name"
              type="text"
              :placeholder="selectedConnectProvider.name"
              autocomplete="off"
            />
          </label>

          <label v-if="selectedConnectProviderRequiresApiKey" class="provider-connect-field">
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

      <div v-if="settingsState.mcpForm.showEditor" class="provider-connect-dialog" role="dialog" aria-modal="true">
        <header class="provider-connect-dialog__top">
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            :aria-label="copy.settings.mcp.backToList"
            @click="$emit('cancel-mcp-edit')"
          >
            ←
          </button>
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            :aria-label="copy.settings.closeAria"
            @click="$emit('cancel-mcp-edit')"
          >
            ×
          </button>
        </header>

        <form class="provider-connect-dialog__body" @submit.prevent="$emit('save-mcp-server')">
          <div class="provider-connect-dialog__title">
            <span class="provider-row__mark" aria-hidden="true">MC</span>
            <h3>{{ settingsState.mcpForm.editingId ? copy.settings.mcp.editTitle : copy.settings.mcp.addTitle }}</h3>
          </div>

          <p>{{ copy.settings.mcp.simpleHint }}</p>

          <label class="provider-connect-field">
            <span>{{ copy.settings.mcp.serverId }}</span>
            <input
              v-model="settingsState.mcpForm.serverId"
              type="text"
              :disabled="Boolean(settingsState.mcpForm.editingId)"
              spellcheck="false"
              autocomplete="off"
            />
          </label>

          <label class="provider-connect-field">
            <span>{{ copy.settings.mcp.transport }}</span>
            <select v-model="settingsState.mcpForm.type">
              <option value="stdio">stdio</option>
              <option value="sse">sse</option>
              <option value="streamableHttp">streamableHttp</option>
            </select>
          </label>

          <label v-if="settingsState.mcpForm.type === 'stdio'" class="provider-connect-field">
            <span>{{ copy.settings.mcp.command }}</span>
            <input v-model="settingsState.mcpForm.command" type="text" spellcheck="false" autocomplete="off" />
          </label>

          <label v-if="settingsState.mcpForm.type === 'stdio'" class="provider-connect-field">
            <span>{{ copy.settings.mcp.args }}</span>
            <textarea v-model="settingsState.mcpForm.argsText" rows="3" spellcheck="false"></textarea>
          </label>

          <label v-if="settingsState.mcpForm.type !== 'stdio'" class="provider-connect-field">
            <span>{{ copy.settings.mcp.url }}</span>
            <input v-model="settingsState.mcpForm.url" type="text" spellcheck="false" autocomplete="off" />
          </label>

          <div class="mcp-editor__toolbar">
            <button class="provider-connect-dialog__advanced" type="button" @click="$emit('toggle-mcp-advanced')">
              {{ settingsState.mcpForm.showAdvanced ? copy.settings.mcp.hideAdvanced : copy.settings.mcp.showAdvanced }}
            </button>
            <button class="provider-connect-dialog__advanced" type="button" @click="$emit('toggle-mcp-json')">
              {{ settingsState.mcpForm.showJsonInput ? copy.settings.mcp.hideJson : copy.settings.mcp.showJson }}
            </button>
          </div>

          <div v-if="settingsState.mcpForm.showJsonInput" class="mcp-editor__json">
            <label class="provider-connect-field">
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

          <div v-if="settingsState.mcpForm.showAdvanced" class="mcp-editor__advanced">
            <div class="mcp-editor__section-title">
              <strong>{{ copy.settings.mcp.advancedTitle }}</strong>
              <span>{{ copy.settings.mcp.advancedHint }}</span>
            </div>

            <label class="provider-connect-field">
              <span>{{ copy.settings.mcp.toolTimeout }}</span>
              <input v-model="settingsState.mcpForm.toolTimeout" type="number" min="1" step="1" />
            </label>

            <label class="provider-connect-field">
              <span>{{ copy.settings.mcp.enabledTools }}</span>
              <textarea v-model="settingsState.mcpForm.enabledToolsText" rows="2" spellcheck="false"></textarea>
            </label>

            <label class="provider-connect-field">
              <span>{{ copy.settings.mcp.env }}</span>
              <textarea v-model="settingsState.mcpForm.envJson" rows="3" spellcheck="false" :placeholder="copy.settings.mcp.jsonPlaceholder"></textarea>
            </label>

            <label class="provider-connect-field">
              <span>{{ copy.settings.mcp.headers }}</span>
              <textarea v-model="settingsState.mcpForm.headersJson" rows="3" spellcheck="false" :placeholder="copy.settings.mcp.jsonPlaceholder"></textarea>
            </label>
          </div>

          <button class="primary-button provider-connect-dialog__submit" type="submit" :disabled="settingsState.mcpLoading">
            {{ settingsState.mcpForm.editingId ? copy.settings.mcp.update : copy.settings.mcp.add }}
          </button>
        </form>
      </div>

      <div v-if="settingsState.cronJobForm.showEditor" class="provider-connect-dialog" role="dialog" aria-modal="true">
        <header class="provider-connect-dialog__top">
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            :aria-label="copy.settings.schedule.backToList"
            @click="$emit('cancel-cron-job-edit')"
          >
            ←
          </button>
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            :aria-label="copy.settings.closeAria"
            @click="$emit('cancel-cron-job-edit')"
          >
            ×
          </button>
        </header>

        <form class="provider-connect-dialog__body" @submit.prevent="$emit('save-cron-job')">
          <div class="provider-connect-dialog__title">
            <span class="provider-row__mark" aria-hidden="true">◷</span>
            <h3>{{ settingsState.cronJobForm.jobId ? copy.settings.schedule.editJobTitle : copy.settings.schedule.newJobTitle }}</h3>
          </div>

          <p>{{ copy.settings.schedule.newJobDescription }}</p>

          <label class="provider-connect-field">
            <span>{{ copy.settings.schedule.jobName }}</span>
            <input v-model="settingsState.cronJobForm.name" type="text" autocomplete="off" />
          </label>

          <label class="provider-connect-field">
            <span>{{ copy.settings.schedule.jobType }}</span>
            <select v-model="settingsState.cronJobForm.mode">
              <option value="cron">{{ copy.settings.schedule.jobTypes.cron }}</option>
              <option value="every">{{ copy.settings.schedule.jobTypes.every }}</option>
              <option value="at">{{ copy.settings.schedule.jobTypes.at }}</option>
            </select>
          </label>

          <label v-if="settingsState.cronJobForm.mode === 'every'" class="provider-connect-field">
            <span>{{ copy.settings.schedule.everySeconds }}</span>
            <input v-model="settingsState.cronJobForm.everySeconds" type="number" min="1" step="1" />
          </label>

          <label v-if="settingsState.cronJobForm.mode === 'cron'" class="provider-connect-field">
            <span>{{ copy.settings.schedule.cronExpression }}</span>
            <input v-model="settingsState.cronJobForm.cronExpr" type="text" spellcheck="false" autocomplete="off" />
          </label>

          <label v-if="settingsState.cronJobForm.mode === 'cron'" class="provider-connect-field">
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

          <label v-if="settingsState.cronJobForm.mode === 'at'" class="provider-connect-field">
            <span>{{ copy.settings.schedule.runAt }}</span>
            <input v-model="settingsState.cronJobForm.at" type="datetime-local" />
          </label>

          <label class="provider-connect-field">
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

          <button class="primary-button provider-connect-dialog__submit" type="submit" :disabled="settingsState.cronJobsLoading">
            {{ settingsState.cronJobForm.jobId ? copy.settings.schedule.updateJob : copy.settings.schedule.createJob }}
          </button>
        </form>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed } from "vue";
import CuratorSettingsPage from "./CuratorSettingsPage.vue";

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
  curatorState: {
    type: Object,
    required: true,
  },
  curatorStatus: {
    type: Object,
    default: null,
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

const selectedConnectProviderRequiresApiKey = computed(() => selectedConnectProvider.value?.requires_api_key !== false);

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

const updateStatusLabel = computed(() => {
  const status = props.settingsState.updateStatus || {};
  if (props.settingsState.updateLoading) {
    return props.copy.settings.general.update.checking;
  }
  if (!status.supported) {
    return props.copy.settings.general.update.unsupported;
  }
  if (status.dirty) {
    return props.copy.settings.general.update.dirty;
  }
  if (status.update_available) {
    return props.copy.settings.general.update.available(status.commits_behind || 0);
  }
  return props.copy.settings.general.update.current;
});

const updateStatusDescription = computed(() => {
  const status = props.settingsState.updateStatus || {};
  if (!status.supported && status.error) {
    return status.error;
  }
  const parts = [];
  if (status.branch) {
    parts.push(`${props.copy.settings.general.update.branch}: ${status.branch}`);
  }
  if (status.current_rev_short) {
    parts.push(`${props.copy.settings.general.update.commit}: ${status.current_rev_short}`);
  }
  if (status.project_root) {
    parts.push(status.project_root);
  }
  return parts.join(" · ") || props.copy.settings.general.update.description;
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

const mediaModelCategories = computed(() => [
  {
    key: "vision",
    mark: "圖",
    title: props.copy.settings.models.mediaCategories.vision.title,
    description: props.copy.settings.models.mediaCategories.vision.description,
  },
  {
    key: "ocr",
    mark: "字",
    title: props.copy.settings.models.mediaCategories.ocr.title,
    description: props.copy.settings.models.mediaCategories.ocr.description,
  },
  {
    key: "speech",
    mark: "音",
    title: props.copy.settings.models.mediaCategories.speech.title,
    description: props.copy.settings.models.mediaCategories.speech.description,
  },
  {
    key: "video",
    mark: "影",
    title: props.copy.settings.models.mediaCategories.video.title,
    description: props.copy.settings.models.mediaCategories.video.description,
  },
]);

const selectedTextProvider = computed(() => {
  const providerId = props.settingsState.selectedTextProviderId;
  return props.settingsState.models.providers.find((provider) => provider.id === providerId) || null;
});

const textProviderModels = computed(() => {
  if (!selectedTextProvider.value) {
    return [];
  }
  const models = Array.isArray(selectedTextProvider.value.models) ? [...selectedTextProvider.value.models] : [];
  const selected = String(props.settingsState.modelSelections[selectedTextProvider.value.id] || "").trim();
  if (selected && !models.includes(selected)) {
    models.unshift(selected);
  }
  return models;
});

const selectedTextModel = computed(() => {
  if (!selectedTextProvider.value) {
    return "";
  }
  return String(props.settingsState.modelSelections[selectedTextProvider.value.id] || selectedTextProvider.value.selected_model || "").trim();
});

const selectedTextCapabilities = computed(() => {
  if (!selectedTextProvider.value || !selectedTextModel.value) {
    return null;
  }
  return selectedTextProvider.value.model_capabilities?.[selectedTextModel.value] || null;
});

const selectedTextRecommendedOptions = computed(() => selectedTextCapabilities.value?.recommended_options || null);

const selectedTextCapabilityBadges = computed(() => {
  const labels = props.copy.settings.models.openRouter.capabilities;
  const capabilities = selectedTextCapabilities.value || {};
  return [
    capabilities.reasoning ? labels.reasoning : null,
    capabilities.vision ? labels.vision : null,
    capabilities.tools ? labels.tools : null,
  ].filter(Boolean);
});

const showOpenRouterOptions = computed(() => (
  selectedTextProvider.value?.provider === "openrouter" &&
  props.settingsState.openRouterOptions[selectedTextProvider.value.id]
));

const codexAuthStatusLabel = computed(() => {
  if (props.settingsState.codexAuthLoading) {
    return props.copy.settings.providers.codexAuth.loading;
  }
  if (!props.settingsState.codexAuth.configured) {
    return props.copy.settings.providers.codexAuth.notConfigured;
  }
  if (props.settingsState.codexAuth.expired) {
    return props.copy.settings.providers.codexAuth.expired;
  }
  return props.copy.settings.providers.codexAuth.configured;
});

const codexAuthDescription = computed(() => {
  const auth = props.settingsState.codexAuth || {};
  if (!auth.configured) {
    return props.copy.settings.providers.codexAuth.description;
  }
  const parts = [];
  if (auth.account_id) {
    parts.push(props.copy.settings.providers.codexAuth.account(auth.account_id));
  }
  if (auth.expires_at) {
    parts.push(props.copy.settings.providers.codexAuth.expires(auth.expires_at));
  }
  return parts.join(" · ") || props.copy.settings.providers.codexAuth.configuredDescription;
});

function mediaProviderModels(category) {
  const providerId = props.settingsState.mediaSelections[category]?.providerId;
  return mediaModelsForProvider(category, providerId, props.settingsState.mediaSelections[category]?.model);
}

function mediaModelsForProvider(category, providerId, selectedModel = "") {
  const provider = props.settingsState.media.providers.find((entry) => entry.id === providerId);
  const mediaModels = provider?.media_models?.[category];
  const models = Array.isArray(mediaModels) ? [...mediaModels] : Array.isArray(provider?.models) ? [...provider.models] : [];
  const selected = String(selectedModel || "").trim();
  if (selected && !models.includes(selected)) {
    models.unshift(selected);
  }
  return models;
}

function syncMediaSelection(category) {
  const selection = props.settingsState.mediaSelections[category];
  if (!selection) {
    return;
  }
  if (!selection.enabled) {
    return;
  }
  if (!selection.providerId) {
    selection.providerId = props.settingsState.media.providers?.[0]?.id || "";
  }
  selection.model = "";
}

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

const mcpToolGroups = computed(() => {
  const toolNames = props.settingsState.mcp.runtime?.tool_names || [];
  const servers = Array.isArray(props.settingsState.mcp.servers) ? props.settingsState.mcp.servers : [];
  const serverIds = servers.map((server) => String(server.id || "").trim()).filter(Boolean);
  const groups = new Map();

  for (const server of servers) {
    const serverId = String(server.id || "").trim();
    if (!serverId) {
      continue;
    }
    groups.set(serverId, {
      serverId,
      serverName: server.name || serverId,
      expanded: props.settingsState.mcpToolGroupsExpanded[serverId] === true,
      tools: [],
    });
  }

  for (const fullName of toolNames) {
    const normalized = String(fullName || "").trim();
    if (!normalized) {
      continue;
    }
    const withoutPrefix = normalized.startsWith("mcp_") ? normalized.slice(4) : normalized;
    const serverId = serverIds
      .filter((candidate) => withoutPrefix.startsWith(`${candidate}_`))
      .sort((left, right) => right.length - left.length)[0] || "unknown";
    const toolName = serverId === "unknown" ? withoutPrefix : withoutPrefix.slice(serverId.length + 1);
    if (!groups.has(serverId)) {
      groups.set(serverId, {
        serverId,
        serverName: serverId === "unknown" ? props.copy.settings.mcp.unknownServer : serverId,
        expanded: props.settingsState.mcpToolGroupsExpanded[serverId] === true,
        tools: [],
      });
    }
    groups.get(serverId).tools.push({ fullName: normalized, name: toolName || normalized });
  }

  return Array.from(groups.values())
    .map((group) => ({
      ...group,
      tools: group.tools.sort((left, right) => left.name.localeCompare(right.name)),
    }))
    .filter((group) => group.tools.length > 0)
    .sort((left, right) => left.serverName.localeCompare(right.serverName));
});

defineEmits([
  "close",
  "select-section",
  "save-connection-settings",
  "toggle-connection",
  "check-update",
  "run-update",
  "begin-channel-connect",
  "cancel-channel-connect",
  "save-channel-connection",
  "disconnect-channel",
  "begin-provider-connect",
  "connect-codex-provider",
  "cancel-provider-connect",
  "save-provider-connection",
  "disconnect-provider",
  "refresh-codex-auth",
  "start-codex-auth-login",
  "logout-codex-auth",
  "select-model",
  "apply-openrouter-recommended-options",
  "save-openrouter-options",
  "save-media-model",
  "begin-mcp-create",
  "save-mcp-server",
  "edit-mcp-server",
  "cancel-mcp-edit",
  "remove-mcp-server",
  "reload-mcp-settings",
  "toggle-mcp-advanced",
  "toggle-mcp-json",
  "toggle-mcp-tool-group",
  "apply-mcp-json",
  "save-schedule-settings",
  "refresh-curator",
  "run-curator-action",
  "begin-cron-job-create",
  "save-cron-job",
  "edit-cron-job",
  "cancel-cron-job-edit",
  "cron-job-action",
]);
</script>
