<template>
  <div v-if="open" class="settings-modal">
    <button
      class="settings-modal__backdrop"
      type="button"
      :aria-label="copy.settings.closeAria"
      @click="$emit('close')"
    ></button>

    <section class="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
      <SettingsNav :copy="copy" :section="section" @select-section="$emit('select-section', $event)" />

      <div class="settings-content">
        <header class="settings-content__header">
          <h2 id="settingsTitle">{{ title }}</h2>
          <button class="settings-panel__close" type="button" :aria-label="copy.settings.closeAria" @click="$emit('close')">
            {{ copy.settings.close }}
          </button>
        </header>

        <GeneralSettingsPage
          v-if="section === 'general'"
          :copy="copy"
          :form="form"
          :settings-state="settingsState"
          :web-session-count="webSessionCount"
          :connection-state="connectionState"
          @save-connection-settings="$emit('save-connection-settings')"
          @toggle-connection="$emit('toggle-connection', $event)"
          @clear-web-sessions="$emit('clear-web-sessions')"
          @check-update="$emit('check-update')"
          @run-update="$emit('run-update')"
        />

        <section v-if="section === 'curator'" class="settings-page">
          <CuratorSettingsPage
            :copy="copy"
            :state="curatorState"
            :status="curatorStatus"
            @refresh-curator="$emit('refresh-curator')"
            @run-curator-action="$emit('run-curator-action', $event)"
          />
        </section>

        <ShortcutsSettingsPage v-if="section === 'shortcuts'" :copy="copy" />

        <section v-if="section === 'channels'" class="settings-page">
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

        <section v-if="section === 'providers'" class="settings-page">
          <p v-if="settingsState.providersLoading" class="settings-inline-status">{{ copy.settings.providers.loading }}</p>
          <p v-if="settingsState.providersError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.providersError }}
          </p>

          <template v-if="showCodexAuthCard">
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
              <div v-if="settingsState.codexAuth.userCode" class="codex-auth-command">
                <span>{{ copy.settings.providers.codexAuth.userCodeLabel }}</span>
                <code>{{ settingsState.codexAuth.userCode }}</code>
                <a v-if="settingsState.codexAuth.verificationUri" :href="settingsState.codexAuth.verificationUri" target="_blank" rel="noreferrer">
                  {{ copy.settings.providers.codexAuth.openVerification }}
                </a>
              </div>
            </div>
            </div>
          </template>

          <template v-if="showCopilotAuthCard">
            <h3>{{ copy.settings.providers.copilotAuth.title }}</h3>
            <p v-if="settingsState.copilotAuthNotice" class="settings-inline-status">{{ settingsState.copilotAuthNotice }}</p>
            <p v-if="settingsState.copilotAuthError" class="settings-inline-status settings-inline-status--error">
              {{ settingsState.copilotAuthError }}
            </p>
            <div class="settings-card provider-card">
            <div class="provider-row provider-row--stacked codex-auth-row">
              <div class="provider-row__content">
                <div class="provider-row__main">
                  <span class="provider-row__mark" aria-hidden="true">Gh</span>
                  <div>
                    <div class="provider-row__title">
                      <strong>{{ copy.settings.providers.copilotAuth.name }}</strong>
                      <span class="provider-row__badge">{{ copilotAuthStatusLabel }}</span>
                    </div>
                    <span>{{ copilotAuthDescription }}</span>
                  </div>
                </div>
                <div class="provider-row__actions">
                  <button class="provider-row__action" type="button" :disabled="settingsState.copilotAuthLoading" @click="$emit('refresh-copilot-auth')">
                    {{ copy.settings.providers.copilotAuth.refresh }}
                  </button>
                  <button class="provider-row__action" type="button" :disabled="settingsState.copilotAuthLoading" @click="$emit('start-copilot-auth-login')">
                    {{ copy.settings.providers.copilotAuth.login }}
                  </button>
                  <button class="provider-row__action" type="button" :disabled="settingsState.copilotAuthLoading || !settingsState.copilotAuth.configured" @click="$emit('logout-copilot-auth')">
                    {{ copy.settings.providers.copilotAuth.logout }}
                  </button>
                </div>
              </div>
              <div v-if="settingsState.copilotAuth.userCode" class="codex-auth-command">
                <span>{{ copy.settings.providers.copilotAuth.userCodeLabel }}</span>
                <code>{{ settingsState.copilotAuth.userCode }}</code>
                <a v-if="settingsState.copilotAuth.verificationUri" :href="settingsState.copilotAuth.verificationUri" target="_blank" rel="noreferrer">
                  {{ copy.settings.providers.copilotAuth.openVerification }}
                </a>
              </div>
            </div>
            </div>
          </template>

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
                    <span v-if="provider.provider === 'openai-codex' && !settingsState.codexAuth.configured" class="provider-row__badge">
                      {{ copy.settings.providers.codexAuth.notConfigured }}
                    </span>
                    <span v-if="provider.provider === 'copilot' && !settingsState.copilotAuth.configured" class="provider-row__badge">
                      {{ copy.settings.providers.copilotAuth.notConfigured }}
                    </span>
                  </div>
                  <span>{{ providerDescription(provider) }}</span>
                  <span v-if="provider.credential_preview" class="provider-row__credential">
                    {{ copy.settings.providers.credentialLabel(provider.credential_label || provider.name, provider.credential_preview, credentialSourceLabel(provider)) }}
                  </span>
                  <span v-else-if="provider.requires_api_key" class="provider-row__credential provider-row__credential--missing">
                    {{ copy.settings.providers.missingCredential }}
                  </span>
                  <label v-if="providerCredentials(provider).length > 1" class="provider-row__select">
                    <span>{{ copy.settings.providers.credentialSelect }}</span>
                    <select
                      :value="providerEffectiveCredentialId(provider)"
                      :disabled="settingsState.providersLoading"
                      @change="$emit('set-provider-credential', provider, $event.target.value)"
                    >
                      <option
                        v-for="credential in providerCredentials(provider)"
                        :key="credential.id"
                        :value="credential.id"
                      >
                        {{ credential.label }} · {{ credential.secret_preview }}
                      </option>
                    </select>
                  </label>
                </div>
              </div>
              <div class="provider-row__actions provider-row__actions--connected">
                <button
                  v-if="providerEffectiveCredentialId(provider)"
                  class="provider-row__action provider-row__action--quiet"
                  type="button"
                  :disabled="settingsState.providersLoading"
                  @click="$emit('delete-credential', provider, providerEffectiveCredentialId(provider))"
                >
                  {{ copy.settings.providers.deleteCredential }}
                </button>
                <button
                  class="provider-row__action provider-row__action--quiet"
                  type="button"
                  :disabled="settingsState.providersLoading"
                  @click="$emit('disconnect-provider', provider)"
                >
                  {{ copy.settings.providers.disconnect }}
                </button>
              </div>
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
                  @click="provider.auth_type === 'openai_codex_oauth' || provider.auth_type === 'github_copilot_oauth' ? $emit('connect-oauth-provider', provider) : $emit('begin-provider-connect', provider)"
                >
                  {{ provider.auth_type === 'openai_codex_oauth' || provider.auth_type === 'github_copilot_oauth' ? copy.settings.providers.connectOAuth : copy.settings.providers.connect }}
                </button>
              </div>

            </div>
          </div>
        </section>

        <section v-if="section === 'models'" class="settings-page">
          <p v-if="settingsState.modelsLoading" class="settings-inline-status">{{ copy.settings.models.loading }}</p>
          <p v-if="settingsState.modelsError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.modelsError }}
          </p>
          <p v-if="settingsState.llmNotice" class="settings-inline-status">{{ settingsState.llmNotice }}</p>
          <p v-if="settingsState.llmError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.llmError }}
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
                  @change="clearSelectedTextProviderModel"
                >
                  <option v-for="provider in settingsState.models.providers" :key="provider.id" :value="provider.id">
                    {{ provider.name }}{{ provider.is_default ? ` (${copy.settings.models.active})` : '' }}
                  </option>
                </select>
              </label>
              <label>
                <span>{{ copy.settings.models.modelChoice }}</span>
                <select v-model="settingsState.modelSelections[selectedTextProvider.id]" :disabled="settingsState.modelsLoading">
                  <option value="">{{ copy.settings.models.noModel }}</option>
                  <template v-if="textProviderModelGroups.length">
                    <optgroup
                      v-for="group in textProviderModelGroups"
                      :key="`${selectedTextProvider.id}:${group.key}`"
                      :label="group.label"
                    >
                      <option v-for="model in group.models" :key="`${selectedTextProvider.id}:${model}`" :value="model">
                        {{ textModelOptionLabel(model) }}
                      </option>
                    </optgroup>
                  </template>
                  <template v-else>
                    <option v-for="model in textProviderModels" :key="`${selectedTextProvider.id}:${model}`" :value="model">
                      {{ textModelOptionLabel(model) }}
                    </option>
                  </template>
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

            <div v-if="showProviderRequestOptions" class="provider-options">
              <div class="provider-options__header">
                <div>
                  <strong>{{ copy.settings.models.providerOptions.title }}</strong>
                  <span>{{ copy.settings.models.providerOptions.description }}</span>
                </div>
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.modelsLoading"
                  @click="providerRequestOptionsExpanded = !providerRequestOptionsExpanded"
                >
                  {{ providerRequestOptionsExpanded ? copy.settings.models.providerOptions.hideOptions : copy.settings.models.providerOptions.showOptions }}
                </button>
              </div>

              <template v-if="providerRequestOptionsExpanded">
                <div class="provider-options__capabilities">
                  <span v-if="selectedTextContextBadge" class="provider-row__badge">
                    {{ selectedTextContextBadge }}
                  </span>
                  <span v-for="capability in selectedTextCapabilityBadges" :key="capability" class="provider-row__badge">
                    {{ capability }}
                  </span>
                  <span class="settings-muted">
                    {{ copy.settings.models.providerOptions.recommendedSummary(selectedTextRecommendedOptions) }}
                  </span>
                </div>

                <div class="provider-options__grid">
                  <div v-if="supportsSelectedProviderRequestOption('reasoning')" class="provider-option-row provider-option-row--switch">
                    <div>
                      <strong>{{ copy.settings.models.providerOptions.reasoningEnabled }}</strong>
                      <span>{{ copy.settings.models.providerOptions.reasoningEnabledDescription }}</span>
                    </div>
                    <input
                      v-model="settingsState.providerRequestOptions[selectedTextProvider.id].reasoningEnabled"
                      class="switch"
                      type="checkbox"
                      :aria-label="copy.settings.models.providerOptions.reasoningEnabled"
                    />
                  </div>
                  <label v-if="supportsSelectedProviderRequestOption('reasoning')" class="provider-option-field">
                    <span>{{ copy.settings.models.providerOptions.reasoningEffort }}</span>
                    <select v-model="settingsState.providerRequestOptions[selectedTextProvider.id].reasoningEffort">
                      <option value="">{{ copy.settings.models.providerOptions.none }}</option>
                      <option value="minimal">minimal</option>
                      <option value="low">low</option>
                      <option value="medium">medium</option>
                      <option value="high">high</option>
                      <option value="xhigh">xhigh</option>
                    </select>
                  </label>
                  <label v-if="supportsSelectedProviderRequestOption('reasoning')" class="provider-option-field">
                    <span>{{ copy.settings.models.providerOptions.reasoningMaxTokens }}</span>
                    <input
                      v-model="settingsState.providerRequestOptions[selectedTextProvider.id].reasoningMaxTokens"
                      type="number"
                      min="1"
                      :placeholder="copy.settings.models.providerOptions.none"
                    />
                  </label>
                  <div v-if="supportsSelectedProviderRequestOption('reasoning')" class="provider-option-row provider-option-row--switch">
                    <div>
                      <strong>{{ copy.settings.models.providerOptions.reasoningExclude }}</strong>
                      <span>{{ copy.settings.models.providerOptions.reasoningExcludeDescription }}</span>
                    </div>
                    <input
                      v-model="settingsState.providerRequestOptions[selectedTextProvider.id].reasoningExclude"
                      class="switch"
                      type="checkbox"
                      :aria-label="copy.settings.models.providerOptions.reasoningExclude"
                    />
                  </div>
                  <label v-if="supportsSelectedProviderRequestOption('provider_sort')" class="provider-option-field">
                    <span>{{ copy.settings.models.providerOptions.providerSort }}</span>
                    <select v-model="settingsState.providerRequestOptions[selectedTextProvider.id].providerSort">
                      <option value="">{{ copy.settings.models.providerOptions.none }}</option>
                      <option value="price">price</option>
                      <option value="throughput">throughput</option>
                      <option value="latency">latency</option>
                    </select>
                  </label>
                  <div v-if="supportsSelectedProviderRequestOption('require_parameters')" class="provider-option-row provider-option-row--switch">
                    <div>
                      <strong>{{ copy.settings.models.providerOptions.requireParameters }}</strong>
                      <span>{{ copy.settings.models.providerOptions.requireParametersDescription }}</span>
                    </div>
                    <input
                      v-model="settingsState.providerRequestOptions[selectedTextProvider.id].requireParameters"
                      class="switch"
                      type="checkbox"
                      :aria-label="copy.settings.models.providerOptions.requireParameters"
                    />
                  </div>
                </div>

                <div class="provider-options__actions">
                  <button
                    class="secondary-button"
                    type="button"
                    :disabled="settingsState.modelsLoading"
                    @click="$emit('apply-provider-recommended-options', selectedTextProvider.id, selectedTextModel)"
                  >
                    {{ copy.settings.models.providerOptions.applyRecommended }}
                  </button>
                  <button
                    class="secondary-button"
                    type="button"
                    :disabled="settingsState.modelsLoading"
                    @click="$emit('save-provider-request-options', selectedTextProvider.id)"
                  >
                    {{ copy.settings.models.providerOptions.save }}
                  </button>
                </div>
              </template>
            </div>
          </div>

          <h3>{{ copy.settings.models.requestTitle }}</h3>
          <div class="settings-card">
            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.models.decodingMode.title }}</strong>
                <span>{{ selectedDecodingModeDescription }}</span>
              </div>
              <select
                v-model="settingsState.llm.decoding_mode"
                :disabled="settingsState.llmLoading"
                @change="handleDecodingModeChange"
              >
                <option v-for="option in decodingModeOptions" :key="option.id" :value="option.id">
                  {{ option.label }}
                </option>
              </select>
            </label>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.models.semanticContract.title }}</strong>
                <span>{{ copy.settings.models.semanticContract.description }}</span>
              </div>
              <input
                v-model="settingsState.llm.semantic_contract_classifier_enabled"
                class="switch"
                type="checkbox"
                :disabled="settingsState.llmLoading"
                :aria-label="copy.settings.models.semanticContract.title"
                @change="$emit('save-llm-settings')"
              />
            </div>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.models.semanticContract.thresholdTitle }}</strong>
                <span>{{ copy.settings.models.semanticContract.thresholdDescription }}</span>
              </div>
              <input
                v-model.number="settingsState.llm.semantic_contract_classifier_confidence_threshold"
                type="number"
                min="0"
                max="1"
                step="0.05"
                :disabled="settingsState.llmLoading || !settingsState.llm.semantic_contract_classifier_enabled"
                @change="$emit('save-llm-settings')"
              />
            </label>

            <template v-if="settingsState.llm.decoding_mode === 'custom'">
              <label class="settings-row settings-row--field">
                <div>
                  <strong>{{ copy.settings.models.decodingFields.temperature.title }}</strong>
                  <span>{{ copy.settings.models.decodingFields.temperature.description }}</span>
                </div>
                <input v-model.number="settingsState.llm.decoding.temperature" type="number" step="0.05" :disabled="settingsState.llmLoading" @change="$emit('save-llm-settings')" />
              </label>
              <label class="settings-row settings-row--field">
                <div>
                  <strong>{{ copy.settings.models.decodingFields.maxTokens.title }}</strong>
                  <span>{{ copy.settings.models.decodingFields.maxTokens.description }}</span>
                </div>
                <input v-model.number="settingsState.llm.decoding.max_tokens" type="number" min="1" step="1" :disabled="settingsState.llmLoading" @change="$emit('save-llm-settings')" />
              </label>
              <label class="settings-row settings-row--field">
                <div>
                  <strong>{{ copy.settings.models.decodingFields.topP.title }}</strong>
                  <span>{{ copy.settings.models.decodingFields.topP.description }}</span>
                </div>
                <input v-model.number="settingsState.llm.decoding.top_p" type="number" min="0" max="1" step="0.05" :disabled="settingsState.llmLoading" @change="$emit('save-llm-settings')" />
              </label>
              <label class="settings-row settings-row--field">
                <div>
                  <strong>{{ copy.settings.models.decodingFields.frequencyPenalty.title }}</strong>
                  <span>{{ copy.settings.models.decodingFields.frequencyPenalty.description }}</span>
                </div>
                <input v-model.number="settingsState.llm.decoding.frequency_penalty" type="number" min="-2" max="2" step="0.1" :disabled="settingsState.llmLoading" @change="$emit('save-llm-settings')" />
              </label>
              <label class="settings-row settings-row--field">
                <div>
                  <strong>{{ copy.settings.models.decodingFields.presencePenalty.title }}</strong>
                  <span>{{ copy.settings.models.decodingFields.presencePenalty.description }}</span>
                </div>
                <input v-model.number="settingsState.llm.decoding.presence_penalty" type="number" min="-2" max="2" step="0.1" :disabled="settingsState.llmLoading" @change="$emit('save-llm-settings')" />
              </label>
            </template>
          </div>

          <h3>{{ copy.settings.models.effectiveRequest.title }}</h3>
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ effectiveRequestProviderLabel }}</strong>
                <span>{{ copy.settings.models.effectiveRequest.description }}</span>
              </div>
              <span class="provider-row__badge">{{ effectiveRequestConfiguredLabel }}</span>
            </div>
            <div v-for="row in effectiveRequestRows" :key="row.key" class="settings-row">
              <div>
                <strong>{{ row.label }}</strong>
                <span>{{ row.value }}</span>
              </div>
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

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.models.enableMediaModel }}</strong>
                <span>{{ category.description }}</span>
              </div>
              <input
                v-model="settingsState.mediaSelections[category.key].enabled"
                class="switch"
                type="checkbox"
                :aria-label="copy.settings.models.enableMediaModel"
                @change="syncMediaSelection(category.key)"
              />
            </div>

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
                  <template v-if="mediaProviderModelGroups(category.key).length">
                    <optgroup
                      v-for="group in mediaProviderModelGroups(category.key)"
                      :key="`${category.key}:${group.key}`"
                      :label="group.label"
                    >
                      <option v-for="model in group.models" :key="`${category.key}:${model}`" :value="model">
                        {{ model }}
                      </option>
                    </optgroup>
                  </template>
                  <template v-else>
                    <option v-for="model in mediaProviderModels(category.key)" :key="`${category.key}:${model}`" :value="model">
                      {{ model }}
                    </option>
                  </template>
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

        <section v-if="section === 'mcp'" class="settings-page">
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

        <section v-if="section === 'schedule'" class="settings-page">
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

        <section v-if="section === 'network'" class="settings-page">
          <p v-if="settingsState.networkLoading" class="settings-inline-status">{{ copy.settings.network.loading }}</p>
          <p v-if="settingsState.networkNotice" class="settings-inline-status">{{ settingsState.networkNotice }}</p>
          <p v-if="settingsState.networkError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.networkError }}
          </p>

          <h3>{{ copy.settings.network.title }}</h3>
          <div class="settings-card settings-card--form">
            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.network.httpProxy.title }}</strong>
                <span>{{ copy.settings.network.httpProxy.description }}</span>
              </div>
              <input
                v-model="settingsState.networkForm.httpProxy"
                type="text"
                :placeholder="copy.settings.network.proxyPlaceholder"
                :disabled="settingsState.networkLoading"
                @keydown.enter.prevent="$emit('save-network-settings')"
              />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.network.httpsProxy.title }}</strong>
                <span>{{ copy.settings.network.httpsProxy.description }}</span>
              </div>
              <input
                v-model="settingsState.networkForm.httpsProxy"
                type="text"
                :placeholder="copy.settings.network.proxyPlaceholder"
                :disabled="settingsState.networkLoading"
                @keydown.enter.prevent="$emit('save-network-settings')"
              />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.network.noProxy.title }}</strong>
                <span>{{ copy.settings.network.noProxy.description }}</span>
              </div>
              <input
                v-model="settingsState.networkForm.noProxy"
                type="text"
                :placeholder="copy.settings.network.noProxy.placeholder"
                :disabled="settingsState.networkLoading"
                @keydown.enter.prevent="$emit('save-network-settings')"
              />
            </label>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.network.currentTitle }}</strong>
                <span>{{ networkSummary }}</span>
              </div>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.networkLoading"
                @click="$emit('save-network-settings')"
              >
                {{ copy.settings.network.save }}
              </button>
            </div>
          </div>

          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.network.scopeTitle }}</strong>
                <span>{{ copy.settings.network.scopeDescription }}</span>
              </div>
            </div>
          </div>
        </section>

        <section v-if="section === 'permissions'" class="settings-page">
          <p v-if="settingsState.permissionsLoading" class="settings-inline-status">{{ copy.settings.permissions.loading }}</p>
          <p v-if="settingsState.permissionsNotice" class="settings-inline-status">{{ settingsState.permissionsNotice }}</p>
          <p v-if="settingsState.permissionsError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.permissionsError }}
          </p>

          <h3>{{ copy.settings.permissions.title }}</h3>
          <div class="settings-card settings-card--form">
            <label class="settings-row settings-row--switch">
              <div>
                <strong>{{ copy.settings.permissions.enabled.title }}</strong>
                <span>{{ copy.settings.permissions.enabled.description }}</span>
              </div>
              <input v-model="settingsState.permissionsForm.enabled" type="checkbox" :disabled="settingsState.permissionsLoading" />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.permissions.approvalMode.title }}</strong>
                <span>{{ copy.settings.permissions.approvalMode.description }}</span>
              </div>
              <select v-model="settingsState.permissionsForm.approvalMode" :disabled="settingsState.permissionsLoading">
                <option value="">{{ copy.settings.permissions.inheritMode }}</option>
                <option v-for="mode in permissionApprovalModeOptions" :key="mode" :value="mode">
                  {{ copy.settings.permissions.approvalModes[mode] || mode }}
                </option>
              </select>
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.permissions.timeout.title }}</strong>
                <span>{{ copy.settings.permissions.timeout.description }}</span>
              </div>
              <input
                v-model.number="settingsState.permissionsForm.approvalTimeoutSeconds"
                type="number"
                min="1"
                step="1"
                :disabled="settingsState.permissionsLoading"
              />
            </label>

            <label class="settings-row settings-row--field settings-row--stacked">
              <div>
                <strong>{{ copy.settings.permissions.allowedTools.title }}</strong>
                <span>{{ copy.settings.permissions.allowedTools.description }}</span>
              </div>
              <textarea
                v-model="settingsState.permissionsForm.allowedTools"
                rows="3"
                :placeholder="copy.settings.permissions.toolListPlaceholder"
                :disabled="settingsState.permissionsLoading"
              ></textarea>
            </label>

            <label class="settings-row settings-row--field settings-row--stacked">
              <div>
                <strong>{{ copy.settings.permissions.deniedTools.title }}</strong>
                <span>{{ copy.settings.permissions.deniedTools.description }}</span>
              </div>
              <textarea
                v-model="settingsState.permissionsForm.deniedTools"
                rows="3"
                :placeholder="copy.settings.permissions.toolListPlaceholder"
                :disabled="settingsState.permissionsLoading"
              ></textarea>
            </label>
          </div>

          <h3>{{ copy.settings.permissions.riskLevelsTitle }}</h3>
          <div class="settings-card settings-card--form">
            <fieldset class="settings-fieldset">
              <legend>{{ copy.settings.permissions.allowedRiskLevels.title }}</legend>
              <p>{{ copy.settings.permissions.allowedRiskLevels.description }}</p>
              <label v-for="riskLevel in permissionRiskLevelOptions" :key="`allow-${riskLevel}`" class="settings-check-row">
                <input
                  v-model="settingsState.permissionsForm.allowedRiskLevels"
                  type="checkbox"
                  :value="riskLevel"
                  :disabled="settingsState.permissionsLoading"
                />
                <span>{{ permissionRiskLabel(riskLevel) }}</span>
              </label>
            </fieldset>

            <fieldset class="settings-fieldset">
              <legend>{{ copy.settings.permissions.deniedRiskLevels.title }}</legend>
              <p>{{ copy.settings.permissions.deniedRiskLevels.description }}</p>
              <label v-for="riskLevel in permissionRiskLevelOptions" :key="`deny-${riskLevel}`" class="settings-check-row">
                <input
                  v-model="settingsState.permissionsForm.deniedRiskLevels"
                  type="checkbox"
                  :value="riskLevel"
                  :disabled="settingsState.permissionsLoading"
                />
                <span>{{ permissionRiskLabel(riskLevel) }}</span>
              </label>
            </fieldset>
          </div>

          <h3>{{ copy.settings.permissions.approvalTitle }}</h3>
          <div class="settings-card settings-card--form">
            <label class="settings-row settings-row--field settings-row--stacked">
              <div>
                <strong>{{ copy.settings.permissions.approvalRequiredTools.title }}</strong>
                <span>{{ copy.settings.permissions.approvalRequiredTools.description }}</span>
              </div>
              <textarea
                v-model="settingsState.permissionsForm.approvalRequiredTools"
                rows="3"
                :placeholder="copy.settings.permissions.toolListPlaceholder"
                :disabled="settingsState.permissionsLoading"
              ></textarea>
            </label>

            <fieldset class="settings-fieldset">
              <legend>{{ copy.settings.permissions.approvalRequiredRiskLevels.title }}</legend>
              <p>{{ copy.settings.permissions.approvalRequiredRiskLevels.description }}</p>
              <label v-for="riskLevel in permissionRiskLevelOptions" :key="`approval-${riskLevel}`" class="settings-check-row">
                <input
                  v-model="settingsState.permissionsForm.approvalRequiredRiskLevels"
                  type="checkbox"
                  :value="riskLevel"
                  :disabled="settingsState.permissionsLoading"
                />
                <span>{{ permissionRiskLabel(riskLevel) }}</span>
              </label>
            </fieldset>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.permissions.currentTitle }}</strong>
                <span>{{ permissionSummary }}</span>
              </div>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.permissionsLoading"
                @click="$emit('save-permissions-settings')"
              >
                {{ copy.settings.permissions.save }}
              </button>
            </div>
          </div>

          <h3>{{ copy.settings.permissions.harnessPreview.title }}</h3>
          <div class="settings-card provider-card">
            <p v-if="settingsState.harnessPolicyPreviewLoading" class="settings-inline-status">
              {{ copy.settings.permissions.harnessPreview.loading }}
            </p>
            <p v-if="settingsState.harnessPolicyPreviewError" class="settings-inline-status settings-inline-status--error">
              {{ settingsState.harnessPolicyPreviewError }}
            </p>
            <div class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.permissions.harnessPreview.effectiveTitle }}</strong>
                <span>{{ copy.settings.permissions.harnessPreview.effectiveDescription }}</span>
              </div>
            </div>
            <div v-for="row in harnessPolicyPreviewRows" :key="row.key" class="provider-row provider-row--stacked">
              <div class="provider-row__content">
                <div class="provider-row__main">
                  <span class="provider-row__mark" aria-hidden="true">{{ row.profileName.slice(0, 2) }}</span>
                  <div>
                    <div class="provider-row__title">
                      <strong>{{ row.title }}</strong>
                      <span class="provider-row__badge">{{ row.policy }}</span>
                    </div>
                    <span>{{ row.description }}</span>
                  </div>
                </div>
              </div>
              <div class="settings-policy-preview">
                <span>{{ copy.settings.permissions.harnessPreview.harnessAllowed(row.harnessAllowed) }}</span>
                <span>{{ copy.settings.permissions.harnessPreview.userAllowed(row.userAllowed) }}</span>
                <span>{{ copy.settings.permissions.harnessPreview.effectiveAllowed(row.effectiveAllowed) }}</span>
                <span>{{ copy.settings.permissions.harnessPreview.denied(row.denied) }}</span>
                <span>{{ copy.settings.permissions.harnessPreview.approval(row.approval) }}</span>
                <span>{{ copy.settings.permissions.harnessPreview.evidence(row.requiredEvidence) }}</span>
                <span>{{ copy.settings.permissions.harnessPreview.verification(row.verification) }}</span>
                <span>{{ copy.settings.permissions.harnessPreview.continuation(row.continuation) }}</span>
              </div>
            </div>
          </div>
        </section>

        <section v-if="section === 'search'" class="settings-page">
          <p v-if="settingsState.searchLoading" class="settings-inline-status">{{ copy.settings.search.loading }}</p>
          <p v-if="settingsState.searchNotice" class="settings-inline-status">{{ settingsState.searchNotice }}</p>
          <p v-if="settingsState.searchError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.searchError }}
          </p>

          <h3>{{ copy.settings.search.title }}</h3>
          <div class="settings-card settings-card--form">
            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.search.provider.title }}</strong>
                <span>{{ copy.settings.search.provider.description }}</span>
              </div>
              <select v-model="settingsState.searchForm.provider" :disabled="settingsState.searchLoading">
                <option v-for="provider in webSearchProviderOptions" :key="provider.id" :value="provider.id">
                  {{ provider.label }}
                </option>
              </select>
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.search.freshness.title }}</strong>
                <span>{{ copy.settings.search.freshness.description }}</span>
              </div>
              <select v-model="settingsState.searchForm.freshness" :disabled="settingsState.searchLoading">
                <option v-for="freshness in webSearchFreshnessOptions" :key="freshness.id" :value="freshness.id">
                  {{ freshness.label }}
                </option>
              </select>
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.search.maxResults.title }}</strong>
                <span>{{ copy.settings.search.maxResults.description }}</span>
              </div>
              <input
                v-model.number="settingsState.searchForm.maxResults"
                type="number"
                min="1"
                max="100"
                :disabled="settingsState.searchLoading"
                @keydown.enter.prevent="$emit('save-search-settings')"
              />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.search.duckduckgoMaxPages.title }}</strong>
                <span>{{ copy.settings.search.duckduckgoMaxPages.description }}</span>
              </div>
              <input
                v-model.number="settingsState.searchForm.duckduckgoMaxPages"
                type="number"
                min="1"
                max="50"
                :disabled="settingsState.searchLoading"
                @keydown.enter.prevent="$emit('save-search-settings')"
              />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.search.searxngMaxPages.title }}</strong>
                <span>{{ copy.settings.search.searxngMaxPages.description }}</span>
              </div>
              <input
                v-model.number="settingsState.searchForm.searxngMaxPages"
                type="number"
                min="1"
                max="50"
                :disabled="settingsState.searchLoading"
                @keydown.enter.prevent="$emit('save-search-settings')"
              />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.search.searxngUrl.title }}</strong>
                <span>{{ copy.settings.search.searxngUrl.description }}</span>
              </div>
              <input
                v-model="settingsState.searchForm.searxngUrl"
                type="text"
                :placeholder="copy.settings.search.searxngUrl.placeholder"
                :disabled="settingsState.searchLoading"
                @keydown.enter.prevent="$emit('save-search-settings')"
              />
            </label>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.search.searxngOptions.title }}</strong>
                <span>{{ copy.settings.search.searxngOptions.description }}</span>
              </div>
              <button
                class="secondary-button"
                type="button"
                :aria-expanded="searxngOptionsExpanded"
                @click="toggleSearxngOptions"
              >
                {{ searxngOptionsExpanded ? copy.settings.search.searxngOptions.collapse : copy.settings.search.searxngOptions.expand }}
              </button>
            </div>

            <div v-show="searxngOptionsExpanded" class="settings-collapsible-section">
              <div class="settings-row">
                <div>
                  <strong>{{ copy.settings.search.searxngOptions.loadTitle }}</strong>
                  <span>{{ copy.settings.search.searxngOptions.loadDescription }}</span>
                  <span v-if="settingsState.searchOptionsNotice">{{ settingsState.searchOptionsNotice }}</span>
                  <span v-if="settingsState.searchOptionsError" class="settings-row__error">{{ settingsState.searchOptionsError }}</span>
                </div>
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.searchLoading || settingsState.searchOptionsLoading"
                  @click="$emit('load-search-searxng-options')"
                >
                  {{ settingsState.searchOptionsLoading ? copy.settings.search.searxngOptions.loading : copy.settings.search.searxngOptions.load }}
                </button>
              </div>

              <div class="settings-row settings-row--field settings-row--choice-list">
                <div>
                  <strong>{{ copy.settings.search.searxngEngines.title }}</strong>
                  <span>{{ copy.settings.search.searxngEngines.description }}</span>
                </div>
                <div v-if="webSearchSearxngEngineOptions.length" class="settings-choice-grid">
                  <label v-for="option in webSearchSearxngEngineOptions" :key="option.id" class="settings-choice">
                    <input
                      v-model="settingsState.searchForm.searxngEngines"
                      type="checkbox"
                      :value="option.id"
                      :disabled="settingsState.searchLoading"
                    />
                    <span>
                      <strong>{{ option.label }}</strong>
                      <small>{{ searxngEngineMeta(option) }}</small>
                    </span>
                  </label>
                </div>
                <p v-else class="settings-empty-inline">{{ copy.settings.search.searxngOptions.emptyEngines }}</p>
              </div>

              <div class="settings-row settings-row--field settings-row--choice-list">
                <div>
                  <strong>{{ copy.settings.search.searxngCategories.title }}</strong>
                  <span>{{ copy.settings.search.searxngCategories.description }}</span>
                </div>
                <div v-if="webSearchSearxngCategoryOptions.length" class="settings-choice-grid">
                  <label v-for="option in webSearchSearxngCategoryOptions" :key="option.id" class="settings-choice">
                    <input
                      v-model="settingsState.searchForm.searxngCategories"
                      type="checkbox"
                      :value="option.id"
                      :disabled="settingsState.searchLoading"
                    />
                    <span>
                      <strong>{{ option.label }}</strong>
                      <small v-if="option.configuredOnly">{{ copy.settings.search.searxngOptions.configuredOnly }}</small>
                    </span>
                  </label>
                </div>
                <p v-else class="settings-empty-inline">{{ copy.settings.search.searxngOptions.emptyCategories }}</p>
              </div>
            </div>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.search.proxy.title }}</strong>
                <span>{{ copy.settings.search.proxy.description }}</span>
              </div>
              <input
                v-model="settingsState.searchForm.proxy"
                type="text"
                :placeholder="copy.settings.search.proxy.placeholder"
                :disabled="settingsState.searchLoading"
                @keydown.enter.prevent="$emit('save-search-settings')"
              />
            </label>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.search.currentTitle }}</strong>
                <span>{{ webSearchSummary }}</span>
              </div>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.searchLoading"
                @click="$emit('save-search-settings')"
              >
                {{ copy.settings.search.save }}
              </button>
            </div>
          </div>

          <h3>{{ copy.settings.search.credentialsTitle }}</h3>
          <div class="settings-card settings-card--form">
            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.search.credentials.jina.title }}</strong>
                <span>{{ copy.settings.search.credentials.description(webSearchCredentialStatus('jina')) }}</span>
              </div>
              <input
                v-model="settingsState.searchForm.jinaApiKey"
                type="password"
                autocomplete="new-password"
                :placeholder="copy.settings.search.credentials.placeholder"
                :disabled="settingsState.searchLoading"
                @keydown.enter.prevent="$emit('save-search-settings')"
              />
            </label>
          </div>
        </section>

        <section v-if="section === 'browser'" class="settings-page">
          <p v-if="settingsState.browserLoading" class="settings-inline-status">{{ copy.settings.browser.loading }}</p>
          <p v-if="settingsState.browserNotice" class="settings-inline-status">{{ settingsState.browserNotice }}</p>
          <p v-if="settingsState.browserError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.browserError }}
          </p>

          <h3>{{ copy.settings.browser.title }}</h3>
          <div class="settings-card settings-card--form">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.browser.enabled.title }}</strong>
                <span>{{ copy.settings.browser.enabled.description }}</span>
              </div>
              <input
                v-model="settingsState.browserForm.enabled"
                class="switch"
                type="checkbox"
                :aria-label="copy.settings.browser.enabled.title"
                :disabled="settingsState.browserLoading"
              />
            </div>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.browser.backend.title }}</strong>
                <span>{{ copy.settings.browser.backend.description }}</span>
              </div>
              <select v-model="settingsState.browserForm.backend" :disabled="settingsState.browserLoading">
                <option v-for="backend in browserBackendOptions" :key="backend.id" :value="backend.id">
                  {{ backend.label }}
                </option>
              </select>
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.browser.cdpUrl.title }}</strong>
                <span>{{ copy.settings.browser.cdpUrl.description }}</span>
              </div>
              <input
                v-model="settingsState.browserForm.cdpUrl"
                type="text"
                :placeholder="copy.settings.browser.cdpUrl.placeholder"
                :disabled="settingsState.browserLoading"
                @keydown.enter.prevent="$emit('save-browser-settings')"
              />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.browser.launchArgs.title }}</strong>
                <span>{{ copy.settings.browser.launchArgs.description }}</span>
              </div>
              <input
                v-model="settingsState.browserForm.launchArgs"
                type="text"
                spellcheck="false"
                :placeholder="copy.settings.browser.launchArgs.placeholder"
                :disabled="settingsState.browserLoading"
                @keydown.enter.prevent="$emit('save-browser-settings')"
              />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.browser.commandTimeout.title }}</strong>
                <span>{{ copy.settings.browser.commandTimeout.description }}</span>
              </div>
              <input
                v-model.number="settingsState.browserForm.commandTimeout"
                type="number"
                min="1"
                max="600"
                :disabled="settingsState.browserLoading"
                @keydown.enter.prevent="$emit('save-browser-settings')"
              />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.browser.sessionTimeout.title }}</strong>
                <span>{{ copy.settings.browser.sessionTimeout.description }}</span>
              </div>
              <input
                v-model.number="settingsState.browserForm.sessionTimeout"
                type="number"
                min="1"
                max="86400"
                :disabled="settingsState.browserLoading"
                @keydown.enter.prevent="$emit('save-browser-settings')"
              />
            </label>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.browser.allowPrivateUrls.title }}</strong>
                <span>{{ copy.settings.browser.allowPrivateUrls.description }}</span>
              </div>
              <input
                v-model="settingsState.browserForm.allowPrivateUrls"
                class="switch"
                type="checkbox"
                :aria-label="copy.settings.browser.allowPrivateUrls.title"
                :disabled="settingsState.browserLoading"
              />
            </div>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.browser.currentTitle }}</strong>
                <span>{{ browserSummary }}</span>
              </div>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.browserLoading"
                @click="$emit('save-browser-settings')"
              >
                {{ copy.settings.browser.save }}
              </button>
            </div>
          </div>

          <h3>{{ copy.settings.browser.test.title }}</h3>
          <div class="settings-card settings-card--form">
            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.browser.test.urlTitle }}</strong>
                <span>{{ copy.settings.browser.test.description }}</span>
              </div>
              <input
                v-model="settingsState.browserForm.testUrl"
                type="url"
                spellcheck="false"
                :placeholder="copy.settings.browser.test.placeholder"
                :disabled="settingsState.browserTestLoading"
                @keydown.enter.prevent="$emit('run-browser-test')"
              />
            </label>
            <div class="settings-row settings-row--update">
              <div>
                <strong>{{ copy.settings.browser.test.currentTitle }}</strong>
                <span>{{ browserTestSummary }}</span>
              </div>
              <div class="settings-row__actions">
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.browserTestLoading || settingsState.browserLoading"
                  @click="$emit('run-browser-test')"
                >
                  {{ settingsState.browserTestLoading ? copy.settings.browser.test.running : copy.settings.browser.test.run }}
                </button>
              </div>
            </div>
          </div>

          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.browser.runtimeTitle }}</strong>
                <span>{{ browserRuntimeStatus }}</span>
                <span v-if="settingsState.browser.runtime?.install_hint">{{ settingsState.browser.runtime.install_hint }}</span>
              </div>
            </div>
          </div>

          <h3>{{ copy.settings.browser.doctor.title }}</h3>
          <div class="settings-card">
            <div class="settings-row settings-row--update">
              <div>
                <strong>{{ copy.settings.browser.doctor.currentTitle }}</strong>
                <span>{{ browserDoctorSummary }}</span>
              </div>
              <div class="settings-row__actions">
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.browserDoctorLoading || settingsState.browserLoading"
                  @click="$emit('run-browser-doctor')"
                >
                  {{ settingsState.browserDoctorLoading ? copy.settings.browser.doctor.running : copy.settings.browser.doctor.run }}
                </button>
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.browserInstallLoading || settingsState.browserDoctorLoading || settingsState.browserLoading"
                  @click="$emit('run-browser-install')"
                >
                  {{ settingsState.browserInstallLoading ? copy.settings.browser.install.running : copy.settings.browser.install.run }}
                </button>
              </div>
            </div>
            <div v-if="settingsState.browserDoctorResult?.checks?.length" class="settings-stack">
              <div v-for="check in settingsState.browserDoctorResult.checks" :key="check.name" class="settings-row">
                <div>
                  <strong>{{ check.command }}</strong>
                  <span>{{ browserDoctorCheckSummary(check) }}</span>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section v-if="section === 'log'" class="settings-page">
          <p v-if="settingsState.logLoading" class="settings-inline-status">{{ copy.settings.log.loading }}</p>
          <p v-if="settingsState.logNotice" class="settings-inline-status">{{ settingsState.logNotice }}</p>
          <p v-if="settingsState.logError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.logError }}
          </p>

          <h3>{{ copy.settings.log.title }}</h3>
          <div class="settings-card settings-card--form">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.log.enabled.title }}</strong>
                <span>{{ copy.settings.log.enabled.description }}</span>
              </div>
              <input
                v-model="settingsState.logForm.enabled"
                class="switch"
                type="checkbox"
                :aria-label="copy.settings.log.enabled.title"
                :disabled="settingsState.logLoading"
              />
            </div>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.log.level.title }}</strong>
                <span>{{ copy.settings.log.level.description }}</span>
              </div>
              <select v-model="settingsState.logForm.level" :disabled="settingsState.logLoading || !settingsState.logForm.enabled">
                <option v-for="level in logLevelOptions" :key="level" :value="level">
                  {{ level }}
                </option>
              </select>
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.log.retention.title }}</strong>
                <span>{{ copy.settings.log.retention.description }}</span>
              </div>
              <input
                v-model.number="settingsState.logForm.retentionDays"
                type="number"
                min="1"
                max="3650"
                :disabled="settingsState.logLoading || !settingsState.logForm.enabled"
              />
            </label>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.log.systemPrompt.title }}</strong>
                <span>{{ copy.settings.log.systemPrompt.description }}</span>
              </div>
              <input
                v-model="settingsState.logForm.logSystemPrompt"
                class="switch"
                type="checkbox"
                :aria-label="copy.settings.log.systemPrompt.title"
                :disabled="settingsState.logLoading || !settingsState.logForm.enabled"
              />
            </div>

            <label class="settings-row settings-row--field">
              <div>
                <strong>{{ copy.settings.log.systemPromptLines.title }}</strong>
                <span>{{ copy.settings.log.systemPromptLines.description }}</span>
              </div>
              <input
                v-model.number="settingsState.logForm.logSystemPromptLines"
                type="number"
                min="0"
                max="3650"
                :disabled="settingsState.logLoading || !settingsState.logForm.enabled || !settingsState.logForm.logSystemPrompt"
              />
            </label>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.log.reasoningDetails.title }}</strong>
                <span>{{ copy.settings.log.reasoningDetails.description }}</span>
              </div>
              <input
                v-model="settingsState.logForm.logReasoningDetails"
                class="switch"
                type="checkbox"
                :aria-label="copy.settings.log.reasoningDetails.title"
                :disabled="settingsState.logLoading || !settingsState.logForm.enabled"
              />
            </div>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.log.currentTitle }}</strong>
                <span>{{ logSummary }}</span>
              </div>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.logLoading"
                @click="$emit('save-log-settings')"
              >
                {{ copy.settings.log.save }}
              </button>
            </div>
          </div>

          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.log.rawResponseTitle }}</strong>
                <span>{{ copy.settings.log.rawResponseDescription }}</span>
              </div>
            </div>
          </div>
        </section>

        <section v-if="section === 'eval'" class="settings-page">
          <p v-if="settingsState.evalLoading" class="settings-inline-status">{{ copy.settings.eval.loading }}</p>
          <p v-if="settingsState.evalNotice" class="settings-inline-status">{{ settingsState.evalNotice }}</p>
          <p v-if="settingsState.evalError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.evalError }}
          </p>

          <h3>{{ copy.settings.eval.title }}</h3>
          <div class="settings-card settings-card--form">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.eval.readinessTitle }}</strong>
                <span>{{ evalReadinessLabel }}</span>
              </div>
              <button class="secondary-button" type="button" :disabled="settingsState.evalLoading" @click="$emit('refresh-eval-status')">
                {{ copy.settings.eval.refresh }}
              </button>
            </div>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.eval.smokeTitle }}</strong>
                <span>{{ copy.settings.eval.smokeDescription }}</span>
              </div>
              <button class="secondary-button" type="button" :disabled="settingsState.evalRunning" @click="$emit('run-eval-smoke')">
                {{ settingsState.evalRunning ? copy.settings.eval.running : copy.settings.eval.runSmoke }}
              </button>
            </div>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.eval.harnessEvalTitle }}</strong>
                <span>{{ copy.settings.eval.harnessEvalDescription }}</span>
              </div>
              <button class="secondary-button" type="button" :disabled="settingsState.harnessEvalRunning" @click="$emit('run-harness-controlled-eval')">
                {{ settingsState.harnessEvalRunning ? copy.settings.eval.running : copy.settings.eval.runHarnessEval }}
              </button>
            </div>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.eval.taskCompletionTitle }}</strong>
                <span>{{ copy.settings.eval.taskCompletionDescription }}</span>
              </div>
              <button class="secondary-button" type="button" :disabled="settingsState.taskCompletionRunning" @click="$emit('run-task-completion-smoke')">
                {{ settingsState.taskCompletionRunning ? copy.settings.eval.running : copy.settings.eval.runTaskCompletionSmoke }}
              </button>
            </div>

            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.eval.liveTaskCompletionTitle }}</strong>
                <span>{{ copy.settings.eval.liveTaskCompletionDescription }}</span>
              </div>
              <button class="secondary-button" type="button" :disabled="settingsState.taskCompletionLiveRunning" @click="$emit('run-task-completion-live')">
                {{ settingsState.taskCompletionLiveRunning ? copy.settings.eval.running : copy.settings.eval.runLiveTaskCompletion }}
              </button>
            </div>
          </div>

          <h3>{{ copy.settings.eval.processCountsTitle }}</h3>
          <div class="settings-card eval-grid">
            <div v-for="entry in evalProcessCounts" :key="entry.state" class="settings-row">
              <div>
                <strong>{{ entry.state }}</strong>
                <span>{{ copy.settings.eval.processCount(entry.count) }}</span>
              </div>
            </div>
          </div>

          <h3>{{ copy.settings.eval.smokeResultsTitle }}</h3>
          <div class="settings-card">
            <div v-if="!settingsState.evalSmoke.checks.length" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.eval.noSmokeTitle }}</strong>
                <span>{{ copy.settings.eval.noSmokeDescription }}</span>
              </div>
            </div>
            <div v-for="check in settingsState.evalSmoke.checks" :key="check.id" class="settings-row">
              <div>
                <strong>{{ check.label }}</strong>
                <span>{{ check.detail }}</span>
              </div>
              <span class="provider-row__badge">{{ check.ok ? copy.settings.eval.pass : copy.settings.eval.fail }}</span>
            </div>
          </div>

          <h3>{{ copy.settings.eval.harnessEvalResultsTitle }}</h3>
          <div class="settings-card">
            <div v-if="!settingsState.harnessEval.cases.length" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.eval.noHarnessEvalTitle }}</strong>
                <span>{{ copy.settings.eval.noHarnessEvalDescription }}</span>
              </div>
            </div>
            <div v-if="settingsState.harnessEval.cases.length" class="settings-row">
              <div>
                <strong>{{ copy.settings.eval.checksSummary(settingsState.harnessEval.summary.passed_checks, settingsState.harnessEval.summary.total_checks) }}</strong>
                <span>{{ copy.settings.eval.historyGroupMeta(settingsState.harnessEval.summary.total_cases, settingsState.harnessEval.summary.passed_cases, settingsState.harnessEval.summary.total_cases - settingsState.harnessEval.summary.passed_cases) }}</span>
              </div>
              <span class="provider-row__badge">{{ settingsState.harnessEval.ok ? copy.settings.eval.pass : copy.settings.eval.fail }}</span>
            </div>
            <div v-for="evalCase in settingsState.harnessEval.cases" :key="evalCase.id" class="settings-row eval-result-row">
              <div>
                <span class="eval-result-row__title">
                  <strong>{{ evalCase.id }}</strong>
                  <span class="provider-row__badge">{{ evalCase.ok ? copy.settings.eval.pass : copy.settings.eval.fail }}</span>
                </span>
                <span>{{ evalCase.profile?.name || copy.settings.eval.none }} · {{ evalCase.policy?.name || copy.settings.eval.none }}</span>
                <span v-if="failedEvalChecks(evalCase).length">{{ failedEvalChecksSummary(evalCase) }}</span>
              </div>
            </div>
          </div>

          <h3>{{ copy.settings.eval.taskCompletionResultsTitle }}</h3>
          <div class="settings-card">
            <div v-if="!settingsState.taskCompletionSmoke.cases.length" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.eval.noTaskCompletionTitle }}</strong>
                <span>{{ copy.settings.eval.noTaskCompletionDescription }}</span>
              </div>
            </div>
            <div v-if="settingsState.taskCompletionSmoke.cases.length" class="settings-row">
              <div>
                <strong>{{ copy.settings.eval.copyAllSmokeDebug }}</strong>
                <span>{{ copy.settings.eval.copyDebugDescription }}</span>
              </div>
              <button
                class="secondary-button"
                type="button"
                @click="copyEvalDebugReport('task-completion-smoke:all', settingsState.taskCompletionSmoke.cases, { source: copy.settings.eval.debugSources.smoke, title: copy.settings.eval.taskCompletionResultsTitle })"
              >
                {{ evalCopyButtonLabel('task-completion-smoke:all', copy.settings.eval.copyAllSmokeDebug) }}
              </button>
            </div>
            <div v-for="evalCase in settingsState.taskCompletionSmoke.cases" :key="evalCase.id" class="settings-row eval-result-row">
              <div>
                <span class="eval-result-row__title">
                  <strong>{{ evalCase.label }}</strong>
                  <span class="provider-row__badge">{{ evalCase.ok ? copy.settings.eval.pass : copy.settings.eval.fail }}</span>
                </span>
                <span>{{ evalResultMeta(evalCase) }}</span>
                <span v-if="evalEntryError(evalCase)">{{ copy.settings.eval.errorLabel(evalEntryError(evalCase)) }}</span>
                <span v-if="evalModelLabel(evalCase)">{{ evalModelLabel(evalCase) }}</span>
                <span v-if="failedEvalChecks(evalCase).length">{{ failedEvalChecksSummary(evalCase) }}</span>
              </div>
              <div class="settings-row__actions">
                <button
                  class="secondary-button"
                  type="button"
                  @click="copyEvalDebugReport(`task-completion-smoke:${evalCase.id}`, [evalCase], { source: copy.settings.eval.debugSources.smoke, title: evalCase.label })"
                >
                  {{ evalCopyButtonLabel(`task-completion-smoke:${evalCase.id}`, copy.settings.eval.copyDebug) }}
                </button>
              </div>
            </div>
          </div>

          <h3>{{ copy.settings.eval.liveTaskCompletionResultsTitle }}</h3>
          <div class="settings-card">
            <div v-if="!settingsState.taskCompletionLive.cases.length" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.eval.noLiveTaskCompletionTitle }}</strong>
                <span>{{ copy.settings.eval.noLiveTaskCompletionDescription }}</span>
              </div>
            </div>
            <div v-if="settingsState.taskCompletionLive.cases.length" class="settings-row">
              <div>
                <strong>{{ copy.settings.eval.copyAllLiveDebug }}</strong>
                <span>{{ copy.settings.eval.copyDebugDescription }}</span>
              </div>
              <button
                class="secondary-button"
                type="button"
                @click="copyEvalDebugReport('task-completion-live:all', settingsState.taskCompletionLive.cases, { source: copy.settings.eval.debugSources.live, title: copy.settings.eval.liveTaskCompletionResultsTitle })"
              >
                {{ evalCopyButtonLabel('task-completion-live:all', copy.settings.eval.copyAllLiveDebug) }}
              </button>
            </div>
            <div v-for="evalCase in settingsState.taskCompletionLive.cases" :key="evalCase.id" class="settings-row eval-result-row">
              <div>
                <span class="eval-result-row__title">
                  <strong>{{ evalCase.label }}</strong>
                  <span class="provider-row__badge">{{ evalCase.ok ? copy.settings.eval.pass : copy.settings.eval.fail }}</span>
                </span>
                <span>{{ evalResultMeta(evalCase, true) }}</span>
                <span v-if="evalEntryError(evalCase)">{{ copy.settings.eval.errorLabel(evalEntryError(evalCase)) }}</span>
                <span v-if="evalModelLabel(evalCase)">{{ evalModelLabel(evalCase) }}</span>
                <span v-if="failedEvalChecks(evalCase).length">{{ failedEvalChecksSummary(evalCase) }}</span>
              </div>
              <div class="settings-row__actions">
                <button
                  class="secondary-button"
                  type="button"
                  @click="copyEvalDebugReport(`task-completion-live:${evalCase.id}`, [evalCase], { source: copy.settings.eval.debugSources.live, title: evalCase.label })"
                >
                  {{ evalCopyButtonLabel(`task-completion-live:${evalCase.id}`, copy.settings.eval.copyDebug) }}
                </button>
              </div>
            </div>
          </div>

          <h3>{{ copy.settings.eval.historyTitle }}</h3>
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.eval.historyRecentTitle }}</strong>
                <span>{{ copy.settings.eval.historyDescription }}</span>
              </div>
              <div class="settings-row__actions">
                <button class="secondary-button" type="button" :disabled="settingsState.taskCompletionHistoryLoading" @click="$emit('refresh-task-completion-history')">
                  {{ copy.settings.eval.refreshHistory }}
                </button>
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="settingsState.taskCompletionHistoryLoading || !settingsState.taskCompletionHistory.length"
                  @click="copyEvalDebugReport('task-completion-history:all', settingsState.taskCompletionHistory, { source: copy.settings.eval.debugSources.history, title: copy.settings.eval.historyTitle })"
                >
                  {{ evalCopyButtonLabel('task-completion-history:all', copy.settings.eval.copyAllHistoryDebug) }}
                </button>
                <button class="secondary-button" type="button" :disabled="settingsState.taskCompletionHistoryLoading || !settingsState.taskCompletionHistory.length" @click="clearTaskCompletionHistory">
                  {{ copy.settings.eval.clearHistory }}
                </button>
              </div>
            </div>
            <p v-if="settingsState.taskCompletionHistoryLoading" class="settings-inline-status">{{ copy.settings.eval.historyLoading }}</p>
            <p v-if="settingsState.taskCompletionHistoryError" class="settings-inline-status settings-inline-status--error">
              {{ settingsState.taskCompletionHistoryError }}
            </p>
            <div v-if="!settingsState.taskCompletionHistory.length && !settingsState.taskCompletionHistoryLoading" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.eval.noHistoryTitle }}</strong>
                <span>{{ copy.settings.eval.noHistoryDescription }}</span>
              </div>
            </div>
            <div v-for="group in taskCompletionHistoryGroups" :key="group.key" class="eval-history-group">
              <button
                class="eval-history-group__toggle"
                type="button"
                :aria-expanded="String(isEvalHistoryGroupExpanded(group.key))"
                @click="toggleEvalHistoryGroup(group.key)"
              >
                <span class="eval-history-group__chevron" aria-hidden="true">
                  {{ isEvalHistoryGroupExpanded(group.key) ? '⌄' : '›' }}
                </span>
                <span class="eval-history-group__main">
                  <strong>{{ copy.settings.eval.historyGroupTitle(formatTimestamp(group.createdAt)) }}</strong>
                  <span>{{ copy.settings.eval.historyGroupMeta(group.total, group.passed, group.failed) }}</span>
                  <span v-if="group.batchId">{{ copy.settings.eval.historyBatchLabel(group.batchId) }}</span>
                  <span v-if="group.modelLabel">{{ group.modelLabel }}</span>
                </span>
                <span class="provider-row__badge">{{ group.ok ? copy.settings.eval.pass : copy.settings.eval.fail }}</span>
              </button>

              <div v-if="isEvalHistoryGroupExpanded(group.key)" class="eval-history-group__items">
                <div class="eval-history-group__batch-actions">
                  <button
                    class="secondary-button"
                    type="button"
                    :disabled="settingsState.taskCompletionHistoryLoading"
                    @click="copyEvalDebugReport(`task-completion-history-group:${group.key}`, group.items, { source: copy.settings.eval.debugSources.batch, title: copy.settings.eval.historyGroupTitle(formatTimestamp(group.createdAt)), batchId: group.batchId, modelLabel: group.modelLabel })"
                  >
                    {{ evalCopyButtonLabel(`task-completion-history-group:${group.key}`, copy.settings.eval.copyBatchDebug) }}
                  </button>
                </div>
                <div v-for="item in group.items" :key="item.eval_id" class="settings-row eval-history-row">
                  <div>
                    <span class="eval-history-row__title">
                      <strong>{{ evalHistoryCaseLabel(item) }}</strong>
                      <span class="provider-row__badge">{{ item.ok ? copy.settings.eval.pass : copy.settings.eval.fail }}</span>
                    </span>
                    <span v-if="item.case_id && item.case_id !== evalHistoryCaseLabel(item)">{{ item.case_id }}</span>
                    <span>{{ copy.settings.eval.historyMeta(formatTimestamp(item.created_at), item.completion_status || copy.settings.eval.none, item.run_id || copy.settings.eval.none) }}</span>
                    <span v-if="evalEntryError(item)">{{ copy.settings.eval.errorLabel(evalEntryError(item)) }}</span>
                    <span>{{ item.response_preview }}</span>
                  </div>
                  <div class="settings-row__actions eval-history-row__actions">
                    <button
                      class="secondary-button"
                      type="button"
                      @click="copyEvalDebugReport(`task-completion-history:${item.eval_id}`, [item], { source: copy.settings.eval.debugSources.history, title: evalHistoryCaseLabel(item) })"
                    >
                      {{ evalCopyButtonLabel(`task-completion-history:${item.eval_id}`, copy.settings.eval.copyDebug) }}
                    </button>
                    <button class="secondary-button" type="button" :disabled="settingsState.taskCompletionHistoryLoading" @click="deleteTaskCompletionHistoryItem(item.eval_id)">
                      {{ copy.settings.eval.deleteHistoryItem }}
                    </button>
                  </div>
                  <div v-if="failedEvalChecks(item).length" class="eval-history-row__failures">
                    <strong>{{ copy.settings.eval.failedChecksForCase(evalHistoryCaseLabel(item)) }}</strong>
                    <ul>
                      <li v-for="(check, index) in failedEvalChecks(item)" :key="`${item.eval_id}:${check.id || index}`">
                        {{ failedEvalCheckText(check) }}
                      </li>
                    </ul>
                    <div class="eval-history-row__comparison">
                      <div>
                        <strong>{{ copy.settings.eval.expectedAnswerTitle }}</strong>
                        <p>{{ evalExpectedSummary(item) }}</p>
                      </div>
                      <div>
                        <strong>{{ copy.settings.eval.actualAnswerTitle }}</strong>
                        <p>{{ evalActualResponse(item) }}</p>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div v-if="evalCopyFallbackOpen" class="eval-copy-fallback">
            <span>{{ copy.settings.eval.debugFallback }}</span>
            <textarea ref="evalCopyTextarea" :value="evalCopyText" rows="8" readonly></textarea>
          </div>

          <h3>{{ copy.settings.eval.metricsTitle }}</h3>
          <div class="settings-card eval-chip-list">
            <span v-for="metric in settingsState.evalStatus.recommended_metrics" :key="metric.id" class="provider-row__badge">
              {{ metric.label }}
            </span>
          </div>

          <h3>{{ copy.settings.eval.scenariosTitle }}</h3>
          <div class="settings-card eval-chip-list">
            <span v-for="scenario in settingsState.evalStatus.recommended_scenarios" :key="scenario.id" class="provider-row__badge">
              {{ scenario.label }}
            </span>
          </div>
        </section>

        <section v-if="section === 'data'" class="settings-page">
          <p v-if="settingsState.dataLoading" class="settings-inline-status">{{ copy.settings.data.loading }}</p>
          <p v-if="settingsState.dataError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.dataError }}
          </p>

          <h3>{{ copy.settings.data.title }}</h3>
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.data.storageType }}</strong>
                <span>{{ dataStorage.type || copy.settings.data.unknown }}</span>
              </div>
              <button class="secondary-button" type="button" :disabled="settingsState.dataLoading" @click="$emit('refresh-data-settings')">
                {{ copy.settings.data.refresh }}
              </button>
            </div>
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.data.storageProvider }}</strong>
                <span>{{ dataStorage.provider || copy.settings.data.unknown }}</span>
              </div>
            </div>
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.data.storagePath }}</strong>
                <span>{{ dataStorage.path || copy.settings.data.noPath }}</span>
              </div>
            </div>
          </div>

          <h3>{{ copy.settings.data.countsTitle }}</h3>
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.data.sessions }}</strong>
                <span>{{ dataCounts.sessions || 0 }}</span>
              </div>
              <span class="provider-row__badge">{{ copy.settings.data.rawSessions(dataCounts.raw_sessions || 0) }}</span>
            </div>
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.data.messages }}</strong>
                <span>{{ dataCounts.messages || 0 }}</span>
              </div>
            </div>
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.data.runs }}</strong>
                <span>{{ dataCounts.runs || 0 }}</span>
              </div>
            </div>
          </div>

          <h3>{{ copy.settings.data.recentTitle }}</h3>
          <div class="settings-card">
            <div v-if="settingsState.dataSessions.length === 0" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.data.noSessionsTitle }}</strong>
                <span>{{ copy.settings.data.noSessionsDescription }}</span>
              </div>
            </div>
            <div v-for="session in settingsState.dataSessions" :key="session.session_id" class="provider-row">
              <div class="provider-row__main">
                <span class="provider-row__mark" aria-hidden="true">{{ session.channel?.slice(0, 2).toUpperCase() || 'OS' }}</span>
                <div>
                  <strong>{{ session.title || session.session_id }}</strong>
                  <span>{{ session.session_id }}</span>
                  <span>{{ copy.settings.data.sessionMeta(session.channel || 'unknown', session.message_count || 0, formatTimestamp(session.updated_at)) }}</span>
                </div>
              </div>
              <div class="provider-row__actions">
                <button class="provider-row__action" type="button" @click="openDataSessionDialog(session)">
                  {{ copy.settings.data.maintain }}
                </button>
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

          <div class="settings-row schedule-editor__deliver">
            <div>
              <strong>{{ copy.settings.schedule.deliver.title }}</strong>
              <span>{{ copy.settings.schedule.deliver.description }}</span>
            </div>
            <input v-model="settingsState.cronJobForm.deliver" class="switch" type="checkbox" :aria-label="copy.settings.schedule.deliver.title" />
          </div>

          <button class="primary-button provider-connect-dialog__submit" type="submit" :disabled="settingsState.cronJobsLoading">
            {{ settingsState.cronJobForm.jobId ? copy.settings.schedule.updateJob : copy.settings.schedule.createJob }}
          </button>
        </form>
      </div>

      <div v-if="selectedDataSession" class="provider-connect-dialog" role="dialog" aria-modal="true">
        <header class="provider-connect-dialog__top">
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            :aria-label="copy.settings.data.backToList"
            @click="closeDataSessionDialog"
          >
            ←
          </button>
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            :aria-label="copy.settings.closeAria"
            @click="closeDataSessionDialog"
          >
            ×
          </button>
        </header>

        <div class="provider-connect-dialog__body">
          <div class="provider-connect-dialog__title">
            <span class="provider-row__mark" aria-hidden="true">{{ selectedDataSession.channel?.slice(0, 2).toUpperCase() || 'OS' }}</span>
            <h3>{{ copy.settings.data.maintenanceTitle }}</h3>
          </div>

          <p>{{ copy.settings.data.maintenanceDescription }}</p>

          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>{{ selectedDataSession.title || selectedDataSession.session_id }}</strong>
                <span>{{ selectedDataSession.session_id }}</span>
              </div>
              <span class="provider-row__badge">{{ selectedDataSession.channel || copy.settings.data.unknown }}</span>
            </div>
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.data.messageCount }}</strong>
                <span>{{ selectedDataSession.message_count || 0 }}</span>
              </div>
            </div>
            <div class="settings-row">
              <div>
                <strong>{{ copy.settings.data.updatedAt }}</strong>
                <span>{{ formatTimestamp(selectedDataSession.updated_at) }}</span>
              </div>
            </div>
          </div>

          <h3>{{ copy.settings.data.previewTitle }}</h3>
          <div class="settings-card">
            <div v-if="!selectedDataSession.messages?.length" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.data.noMessagesTitle }}</strong>
                <span>{{ copy.settings.data.noMessagesDescription }}</span>
              </div>
            </div>
            <div v-for="message in selectedDataSession.messages" :key="`${selectedDataSession.session_id}:${message.created_at}:${message.role}`" class="settings-row">
              <div>
                <strong>{{ copy.settings.data.messageRole(message.role) }}</strong>
                <span>{{ previewMessage(message.content) }}</span>
                <span>{{ formatTimestamp(message.created_at) }}</span>
              </div>
            </div>
          </div>

          <h3>{{ copy.settings.data.timelineTitle }}</h3>
          <p v-if="settingsState.dataTimelineLoading" class="settings-inline-status">{{ copy.settings.data.timelineLoading }}</p>
          <p v-if="settingsState.dataTimelineError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.dataTimelineError }}
          </p>
          <div class="settings-card">
            <div v-if="dataTimelineEntries.length === 0 && !settingsState.dataTimelineLoading" class="provider-row provider-row--empty">
              <div>
                <strong>{{ copy.settings.data.noTimelineTitle }}</strong>
                <span>{{ copy.settings.data.noTimelineDescription }}</span>
              </div>
            </div>
            <div v-else class="data-timeline-table-wrap">
              <table class="data-timeline-table">
                <thead>
                  <tr>
                    <th>{{ copy.settings.data.timelineColumns.time }}</th>
                    <th>{{ copy.settings.data.timelineColumns.type }}</th>
                    <th>{{ copy.settings.data.timelineColumns.summary }}</th>
                    <th>{{ copy.settings.data.timelineColumns.status }}</th>
                    <th>{{ copy.settings.data.timelineColumns.actions }}</th>
                  </tr>
                </thead>
                <tbody>
                  <template v-for="entry in dataTimelineEntries" :key="timelineEntryKey(entry)">
                    <tr class="data-timeline-table__row" :class="{ 'data-timeline-table__row--expanded': isTimelineEntryExpanded(entry) }">
                      <td>{{ formatTimestamp(entry.created_at) }}</td>
                      <td>
                        <span class="provider-row__badge">{{ timelineEntryLabel(entry) }}</span>
                      </td>
                      <td>
                        <strong>{{ timelineEntryDetail(entry) }}</strong>
                        <span v-if="timelineEntryContent(entry).length">{{ copy.settings.data.timelineItemCount(timelineEntryContent(entry).length) }}</span>
                      </td>
                      <td>{{ entry.status || copy.settings.data.timelineNoStatus }}</td>
                      <td>
                        <button class="provider-row__action" type="button" @click="toggleTimelineEntry(entry)">
                          {{ isTimelineEntryExpanded(entry) ? copy.settings.data.collapseTimeline : copy.settings.data.expandTimeline }}
                        </button>
                      </td>
                    </tr>
                    <tr v-if="isTimelineEntryExpanded(entry)" class="data-timeline-table__details">
                      <td colspan="5">
                        <div class="data-timeline-table__details-grid">
                          <section>
                            <strong>{{ copy.settings.data.timelineTextTitle }}</strong>
                            <p>{{ timelineEntryFullText(entry) }}</p>
                          </section>
                          <section v-if="timelineEntryContent(entry).length">
                            <strong>{{ copy.settings.data.timelineItemsTitle }}</strong>
                            <ul>
                              <li v-for="item in timelineEntryContent(entry)" :key="`${timelineEntryKey(entry)}:${item.created_at}:${item.type}:${item.title}`">
                                {{ timelineItemLabel(item) }}
                              </li>
                            </ul>
                          </section>
                          <section>
                            <strong>{{ copy.settings.data.timelineRawTitle }}</strong>
                            <pre>{{ timelineEntryJson(entry) }}</pre>
                          </section>
                        </div>
                      </td>
                    </tr>
                  </template>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import CuratorSettingsPage from "./CuratorSettingsPage.vue";
import GeneralSettingsPage from "./GeneralSettingsPage.vue";
import SettingsNav from "./SettingsNav.vue";
import ShortcutsSettingsPage from "./ShortcutsSettingsPage.vue";
import {
  providerSupportsModelMetadata,
  providerSupportsRequestOption,
  providerSupportsRequestOptions,
} from "../composables/settingsNormalizers";

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
  webSessionCount: {
    type: Number,
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

const selectedDataSession = ref(null);
const expandedTimelineEntryKeys = ref(new Set());
const expandedEvalHistoryGroupKeys = ref(new Set());
const evalCopyState = ref({ key: "", status: "idle" });
const evalCopyFallbackOpen = ref(false);
const evalCopyText = ref("");
const evalCopyTextarea = ref(null);
const providerRequestOptionsExpanded = ref(false);
const searxngOptionsExpanded = ref(false);
const EVAL_HISTORY_GROUP_WINDOW_SECONDS = 10 * 60;
let evalCopyResetTimer = null;

onBeforeUnmount(() => {
  clearEvalCopyResetTimer();
});

watch(
  () => [
    props.settingsState.taskCompletionHistory?.length || 0,
    props.settingsState.taskCompletionSmoke?.cases?.length || 0,
    props.settingsState.taskCompletionLive?.cases?.length || 0,
  ],
  () => {
    if (evalCopyFallbackOpen.value && isEvalCopySourceEmpty(evalCopyState.value.key)) {
      resetEvalCopyFallback();
    }
  },
);

function clearEvalCopyResetTimer() {
  if (evalCopyResetTimer) {
    clearTimeout(evalCopyResetTimer);
    evalCopyResetTimer = null;
  }
}

function resetEvalCopyFallback() {
  clearEvalCopyResetTimer();
  evalCopyState.value = { key: "", status: "idle" };
  evalCopyFallbackOpen.value = false;
  evalCopyText.value = "";
}

function isEvalCopySourceEmpty(key) {
  if (String(key || "").startsWith("task-completion-history")) {
    return !props.settingsState.taskCompletionHistory?.length;
  }
  if (String(key || "").startsWith("task-completion-smoke")) {
    return !props.settingsState.taskCompletionSmoke?.cases?.length;
  }
  if (String(key || "").startsWith("task-completion-live")) {
    return !props.settingsState.taskCompletionLive?.cases?.length;
  }
  return false;
}

function clearTaskCompletionHistory() {
  resetEvalCopyFallback();
  emit("clear-task-completion-history");
}

function deleteTaskCompletionHistoryItem(evalId) {
  resetEvalCopyFallback();
  emit("delete-task-completion-history-item", evalId);
}

function openDataSessionDialog(session) {
  selectedDataSession.value = session;
  expandedTimelineEntryKeys.value = new Set();
  emit("load-data-session-timeline", session?.session_id || "");
}

function closeDataSessionDialog() {
  selectedDataSession.value = null;
  expandedTimelineEntryKeys.value = new Set();
}

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

const selectedConnectProviderRequiresApiKey = computed(
  () => selectedConnectProvider.value?.requires_api_key !== false || selectedConnectProvider.value?.api_key_optional === true,
);

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

const dataStorage = computed(() => props.settingsState.dataStatus?.storage || {});
const dataCounts = computed(() => props.settingsState.dataStatus?.counts || {});
const dataTimelineEntries = computed(() => props.settingsState.dataTimeline?.entries || []);
const taskCompletionHistoryGroups = computed(() => {
  const groups = [];
  const groupsByBatchId = new Map();
  for (const item of props.settingsState.taskCompletionHistory || []) {
    const createdAt = Number(item?.created_at || 0);
    const batchId = evalHistoryBatchId(item);
    const modelKey = evalHistoryModelKey(item);
    if (batchId) {
      let group = groupsByBatchId.get(batchId);
      if (!group) {
        group = {
          key: `batch:${batchId}`,
          batchId,
          createdAt,
          oldestCreatedAt: createdAt,
          modelKey,
          modelLabel: evalModelLabel(item),
          items: [],
        };
        groupsByBatchId.set(batchId, group);
        groups.push(group);
      }
      group.items.push(item);
      group.oldestCreatedAt = Math.min(Number(group.oldestCreatedAt || createdAt), createdAt);
      group.createdAt = Math.max(Number(group.createdAt || createdAt), createdAt);
      continue;
    }

    const previous = groups.at(-1);
    const previousOldest = Number(previous?.oldestCreatedAt || previous?.createdAt || 0);
    const shouldStartGroup = !previous
      || previous.batchId
      || previous.modelKey !== modelKey
      || Math.abs(previousOldest - createdAt) > EVAL_HISTORY_GROUP_WINDOW_SECONDS;

    if (shouldStartGroup) {
      groups.push({
        key: item?.eval_id || `${createdAt}:${groups.length}`,
        createdAt,
        oldestCreatedAt: createdAt,
        modelKey,
        modelLabel: evalModelLabel(item),
        items: [item],
      });
    } else {
      previous.items.push(item);
      previous.oldestCreatedAt = createdAt;
    }
  }

  return groups.map((group) => {
    const passed = group.items.filter((item) => item?.ok).length;
    const total = group.items.length;
    return {
      ...group,
      total,
      passed,
      failed: total - passed,
      ok: passed === total,
    };
  });
});

function formatTimestamp(value) {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return props.copy.settings.data.never;
  }
  return new Date(numeric * 1000).toLocaleString();
}

function failedEvalChecks(entry) {
  return Array.isArray(entry?.checks) ? entry.checks.filter((check) => check?.ok === false) : [];
}

function failedEvalCheckText(check) {
  const id = String(check?.id || props.copy.settings.eval.none).trim();
  const label = String(check?.label || "").trim();
  const detail = String(check?.detail || "").trim();
  const item = failedEvalCheckItem(id, label);
  const hint = failedEvalCheckHint(id);
  return props.copy.settings.eval.failedCheckItem(item, detail || label || id, hint);
}

function evalHistoryCaseLabel(entry) {
  return String(
    entry?.case_label
      || entry?.metadata?.case_label
      || entry?.label
      || entry?.case_id
      || entry?.id
      || props.copy.settings.eval.none,
  ).trim();
}

function evalExpectedSummary(entry) {
  return String(entry?.expected_summary || entry?.metadata?.expected_summary || props.copy.settings.eval.none).trim();
}

function evalActualResponse(entry) {
  return String(entry?.actual_response || entry?.metadata?.actual_response || entry?.response_preview || props.copy.settings.eval.none).trim();
}

function evalEntryError(entry) {
  return String(entry?.error || entry?.metadata?.error || "").trim();
}

function failedEvalCheckItem(checkId, fallbackLabel = "") {
  const labels = props.copy.settings.eval.failedCheckItems;
  if (checkId === "completion_status") {
    return labels.completionStatus;
  }
  if (checkId === "response_present") {
    return labels.responsePresent;
  }
  if (checkId === "tool_errors") {
    return labels.toolErrors;
  }
  if (checkId === "run_trace") {
    return labels.runTrace;
  }
  if (checkId === "max_response_chars") {
    return labels.maxResponseChars;
  }
  if (checkId === "exact_response") {
    return labels.exactResponse;
  }
  if (checkId === "expected_non_empty_lines") {
    return labels.expectedLines;
  }
  if (checkId.startsWith("must_end_with_")) {
    return labels.mustEndWith;
  }
  if (checkId.startsWith("must_include_")) {
    return labels.mustInclude;
  }
  if (checkId.startsWith("must_not_include_")) {
    return labels.mustNotInclude;
  }
  return fallbackLabel || labels.generic;
}

function failedEvalCheckHint(checkId) {
  const hints = props.copy.settings.eval.failedCheckHints;
  if (checkId === "completion_status") {
    return hints.completionStatus;
  }
  if (checkId === "tool_errors" || checkId === "run_trace") {
    return hints.execution;
  }
  if (checkId === "response_present") {
    return hints.responsePresent;
  }
  if (
    checkId === "max_response_chars" ||
    checkId === "exact_response" ||
    checkId === "expected_non_empty_lines" ||
    checkId.startsWith("must_end_with_") ||
    checkId.startsWith("must_include_") ||
    checkId.startsWith("must_not_include_")
  ) {
    return hints.llmOutput;
  }
  return "";
}

function failedEvalChecksSummary(entry) {
  const failed = failedEvalChecks(entry);
  return props.copy.settings.eval.failedChecks(failed.length, failed.map(failedEvalCheckText).join("; "));
}

function evalResultMeta(entry, includeRun = false) {
  const parts = [evalChecksSummary(entry)];
  if (includeRun && entry?.run_id) {
    parts.push(props.copy.settings.eval.evalRun(entry.run_id));
  }
  if (entry?.response_preview) {
    parts.push(String(entry.response_preview).trim());
  }
  return parts.filter(Boolean).join(" ");
}

function evalChecksSummary(entry) {
  const score = entry?.score || entry?.summary?.score || {};
  const passed = Number(score.passed);
  const total = Number(score.total);
  if (Number.isFinite(passed) && Number.isFinite(total) && total > 0) {
    return props.copy.settings.eval.checksSummary(passed, total);
  }
  const checks = Array.isArray(entry?.checks) ? entry.checks : [];
  if (checks.length) {
    return props.copy.settings.eval.checksSummary(
      checks.filter((check) => check?.ok).length,
      checks.length,
    );
  }
  return evalEntrySummary(entry);
}

function evalModelInfo(entry) {
  return entry?.model || entry?.metadata?.model || {};
}

function evalModelLabel(entry) {
  const modelInfo = evalModelInfo(entry);
  const model = String(modelInfo.model || "").trim();
  const provider = String(modelInfo.provider_id || modelInfo.provider || "").trim();
  if (!model && !provider) {
    return "";
  }
  return props.copy.settings.eval.modelLabel(provider || props.copy.settings.eval.none, model || props.copy.settings.eval.none);
}

function evalHistoryModelKey(entry) {
  const modelInfo = evalModelInfo(entry);
  return [modelInfo.provider_id || modelInfo.provider || "", modelInfo.model || ""].map((value) => String(value || "").trim()).join("/");
}

function evalHistoryBatchId(entry) {
  return String(entry?.batch_id || entry?.metadata?.batch_id || "").trim();
}

function evalCopyButtonLabel(key, fallbackLabel) {
  if (evalCopyState.value.key !== key) {
    return fallbackLabel;
  }
  if (evalCopyState.value.status === "copying") {
    return props.copy.settings.eval.copyingDebug;
  }
  if (evalCopyState.value.status === "copied") {
    return props.copy.settings.eval.debugCopied;
  }
  if (evalCopyState.value.status === "manual") {
    return props.copy.settings.eval.manualCopyDebug;
  }
  return fallbackLabel;
}

async function copyEvalDebugReport(key, entries, context = {}) {
  const normalizedEntries = Array.isArray(entries) ? entries.filter(Boolean) : [];
  if (!normalizedEntries.length) {
    return;
  }
  const report = buildEvalDebugReport(normalizedEntries, context);
  evalCopyText.value = report;
  evalCopyState.value = { key, status: "copying" };
  if (evalCopyResetTimer) {
    clearTimeout(evalCopyResetTimer);
    evalCopyResetTimer = null;
  }
  try {
    if (typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
      throw new Error("Clipboard API unavailable");
    }
    await navigator.clipboard.writeText(report);
    evalCopyFallbackOpen.value = false;
    evalCopyState.value = { key, status: "copied" };
    evalCopyResetTimer = setTimeout(() => {
      evalCopyState.value = { key: "", status: "idle" };
    }, 1800);
  } catch {
    evalCopyState.value = { key, status: "manual" };
    evalCopyFallbackOpen.value = true;
    await nextTick();
    evalCopyTextarea.value?.focus();
    evalCopyTextarea.value?.select();
  }
}

function buildEvalDebugReport(entries, context = {}) {
  const debug = props.copy.settings.eval.debugReport;
  const total = entries.length;
  const passed = entries.filter((entry) => entry?.ok).length;
  const failed = total - passed;
  const lines = [
    `# ${context.title || props.copy.settings.eval.debugReportTitle}`,
    "",
    `- ${debug.generated}: ${formatTimestamp(Date.now() / 1000)}`,
    `- ${debug.source}: ${context.source || props.copy.settings.eval.none}`,
    `- ${debug.total}: ${props.copy.settings.eval.historyGroupMeta(total, passed, failed)}`,
  ];

  if (context.batchId) {
    lines.push(`- ${debug.batch}: ${context.batchId}`);
  }
  if (context.modelLabel) {
    lines.push(`- ${debug.model}: ${context.modelLabel}`);
  }

  for (const entry of entries) {
    lines.push("", `## ${debug.case}: ${evalHistoryCaseLabel(entry)}`);
    lines.push(`- ${debug.status}: ${entry?.ok ? props.copy.settings.eval.pass : props.copy.settings.eval.fail}`);
    lines.push(`- ${debug.caseId}: ${evalEntryCaseId(entry)}`);
    if (entry?.eval_id) {
      lines.push(`- ${debug.evalId}: ${entry.eval_id}`);
    }
    if (evalHistoryBatchId(entry)) {
      lines.push(`- ${debug.batch}: ${evalHistoryBatchId(entry)}`);
    }
    if (evalModelLabel(entry)) {
      lines.push(`- ${debug.model}: ${evalModelLabel(entry)}`);
    }
    if (entry?.response_source || entry?.metadata?.response_source) {
      lines.push(`- ${debug.responseSource}: ${entry.response_source || entry.metadata.response_source}`);
    }
    if (entry?.session_id) {
      lines.push(`- ${debug.session}: ${entry.session_id}`);
    }
    if (entry?.run_id) {
      lines.push(`- ${debug.run}: ${entry.run_id}`);
    }
    lines.push(`- ${debug.completionStatus}: ${entry?.completion_status || props.copy.settings.eval.none}`);
    if (evalEntryError(entry)) {
      lines.push(`- ${debug.error}: ${evalEntryError(entry)}`);
    }
    lines.push(`- ${debug.summary}: ${evalEntrySummary(entry)}`);
    lines.push(`- ${debug.responsePreview}: ${entry?.response_preview || props.copy.settings.eval.none}`);
    lines.push("", `### ${debug.prompt}`, evalEntryPrompt(entry));
    lines.push("", `### ${debug.failedChecks}`);
    const failedChecks = failedEvalChecks(entry);
    if (failedChecks.length) {
      lines.push(...failedChecks.map((check) => `- ${failedEvalCheckText(check)}`));
    } else {
      lines.push(`- ${debug.noFailedChecks}`);
    }
    lines.push("", `### ${debug.expected}`, evalExpectedSummary(entry));
    lines.push("", `### ${debug.actual}`, evalActualResponse(entry));
    lines.push("", `### ${debug.allChecks}`, "```json", evalEntryChecksJson(entry), "```");
  }

  return lines.join("\n");
}

function evalEntryCaseId(entry) {
  return String(entry?.case_id || entry?.id || props.copy.settings.eval.none).trim();
}

function evalEntryPrompt(entry) {
  return String(entry?.prompt || entry?.metadata?.prompt || props.copy.settings.eval.none).trim();
}

function evalEntrySummary(entry) {
  if (typeof entry?.summary === "string") {
    return entry.summary || props.copy.settings.eval.none;
  }
  if (entry?.summary?.text) {
    return String(entry.summary.text).trim();
  }
  if (entry?.score) {
    return `${entry.score.passed || 0}/${entry.score.total || 0} checks passed.`;
  }
  if (entry?.summary?.score) {
    return `${entry.summary.score.passed || 0}/${entry.summary.score.total || 0} checks passed.`;
  }
  return props.copy.settings.eval.none;
}

function evalEntryChecksJson(entry) {
  try {
    return JSON.stringify(entry?.checks || [], null, 2);
  } catch {
    return String(entry?.checks || props.copy.settings.eval.none);
  }
}

function isEvalHistoryGroupExpanded(groupKey) {
  return expandedEvalHistoryGroupKeys.value.has(groupKey);
}

function toggleEvalHistoryGroup(groupKey) {
  const nextKeys = new Set(expandedEvalHistoryGroupKeys.value);
  if (nextKeys.has(groupKey)) {
    nextKeys.delete(groupKey);
  } else {
    nextKeys.add(groupKey);
  }
  expandedEvalHistoryGroupKeys.value = nextKeys;
}

function previewMessage(content) {
  const text = String(content || "").replace(/\s+/g, " ").trim();
  if (!text) {
    return props.copy.settings.data.emptyMessage;
  }
  return text.length > 120 ? `${text.slice(0, 120)}...` : text;
}

function timelineEntryContent(entry) {
  return Array.isArray(entry?.content) ? entry.content : [];
}

function timelineEntryKey(entry) {
  return entry?.entry_id || `${entry?.created_at || 0}:${entry?.entry_type || entry?.role || "entry"}:${entry?.run_id || ""}`;
}

function isTimelineEntryExpanded(entry) {
  return expandedTimelineEntryKeys.value.has(timelineEntryKey(entry));
}

function toggleTimelineEntry(entry) {
  const key = timelineEntryKey(entry);
  const next = new Set(expandedTimelineEntryKeys.value);
  if (next.has(key)) {
    next.delete(key);
  } else {
    next.add(key);
  }
  expandedTimelineEntryKeys.value = next;
}

function timelineEntryLabel(entry) {
  if (entry?.run_id) {
    return props.copy.settings.data.timelineRun(entry.run_id);
  }
  return props.copy.settings.data.messageRole(entry?.role || entry?.entry_type || "message");
}

function timelineEntryDetail(entry) {
  if (entry?.text) {
    return previewMessage(entry.text);
  }
  const textItem = timelineEntryContent(entry).find((item) => item?.type === "text" && item?.text);
  if (textItem) {
    return previewMessage(textItem.text);
  }
  const itemCount = timelineEntryContent(entry).length;
  if (itemCount > 0) {
    return props.copy.settings.data.timelineItemCount(itemCount);
  }
  return props.copy.settings.data.emptyMessage;
}

function timelineEntryFullText(entry) {
  if (entry?.text) {
    return String(entry.text || "");
  }
  const textItems = timelineEntryContent(entry)
    .filter((item) => item?.text)
    .map((item) => String(item.text || "").trim())
    .filter(Boolean);
  return textItems.join("\n\n") || timelineEntryDetail(entry);
}

function timelineEntryJson(entry) {
  try {
    return JSON.stringify(entry, null, 2);
  } catch {
    return String(entry || "");
  }
}

function timelineItemLabel(item) {
  const type = props.copy.settings.data.timelineItemType(item?.type || "event");
  const title = item?.title || item?.detail || item?.text || "";
  const status = item?.status ? ` · ${item.status}` : "";
  return `${type}${title ? `: ${previewMessage(title)}` : ""}${status}`;
}

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

function formatCompactTokenCount(value) {
  const tokens = Number(value);
  if (!Number.isFinite(tokens) || tokens <= 0) {
    return "";
  }
  if (tokens >= 1_000_000) {
    const millions = tokens / 1_000_000;
    return `${Number(millions.toFixed(millions >= 10 ? 0 : 1))}M`;
  }
  if (tokens >= 1_000) {
    return `${Math.round(tokens / 1_000)}K`;
  }
  return String(Math.round(tokens));
}

function modelContextLabel(provider, model) {
  const contextLength = provider?.model_metadata?.[model]?.context_length;
  const formatted = formatCompactTokenCount(contextLength);
  return formatted ? props.copy.settings.models.providerOptions.contextLength(formatted) : "";
}

function textModelOptionLabel(model) {
  const provider = selectedTextProvider.value;
  const context = providerSupportsModelMetadata(provider, "context_length") ? modelContextLabel(provider, model) : "";
  const label = [model, context].filter(Boolean).join(" · ");
  return provider?.is_default && provider.selected_model === model ? `${label} (${props.copy.settings.models.active})` : label;
}

function clearSelectedTextProviderModel() {
  const providerId = props.settingsState.selectedTextProviderId;
  if (providerId) {
    props.settingsState.modelSelections[providerId] = "";
  }
}

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

const modelFamilyLabels = {
  "01-ai": "01.AI",
  ai21: "AI21",
  amazon: "Amazon",
  anthropic: "Anthropic",
  cohere: "Cohere",
  deepseek: "DeepSeek",
  google: "Google",
  meta: "Meta",
  "meta-llama": "Meta",
  microsoft: "Microsoft",
  minimax: "MiniMax",
  mistralai: "Mistral",
  moonshotai: "Moonshot AI",
  nousresearch: "Nous Research",
  nvidia: "NVIDIA",
  openai: "OpenAI",
  perplexity: "Perplexity",
  qwen: "Qwen",
  xai: "xAI",
  zai: "Z.AI",
};

function titleCaseModelFamily(value) {
  return value
    .split(/[-_]+/)
    .filter(Boolean)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function slashModelFamily(model) {
  const normalized = String(model || "").trim();
  const separator = normalized.indexOf("/");
  if (separator <= 0) {
    return { key: "custom", label: props.copy.settings.models.providerOptions.customGroup };
  }
  const family = normalized.slice(0, separator).trim().toLowerCase();
  return {
    key: family,
    label: modelFamilyLabels[family] || titleCaseModelFamily(family) || props.copy.settings.models.providerOptions.otherGroup,
  };
}

function slashModelGroups(models) {
  const groups = new Map();
  for (const model of models) {
    const family = slashModelFamily(model);
    if (!groups.has(family.key)) {
      groups.set(family.key, { key: family.key, label: family.label, models: [] });
    }
    groups.get(family.key).models.push(model);
  }
  return Array.from(groups.values());
}

function hasSlashModelIds(models) {
  return models.some((model) => String(model || "").includes("/"));
}

const textProviderModelGroups = computed(() => {
  if (!hasSlashModelIds(textProviderModels.value)) {
    return [];
  }
  return slashModelGroups(textProviderModels.value);
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

const selectedTextContextBadge = computed(() => {
  if (!providerSupportsModelMetadata(selectedTextProvider.value, "context_length") || !selectedTextModel.value) {
    return "";
  }
  return modelContextLabel(selectedTextProvider.value, selectedTextModel.value);
});

const selectedTextCapabilityBadges = computed(() => {
  const labels = props.copy.settings.models.providerOptions.capabilities;
  const capabilities = selectedTextCapabilities.value || {};
  return [
    capabilities.reasoning ? labels.reasoning : null,
    capabilities.vision ? labels.vision : null,
    capabilities.tools ? labels.tools : null,
  ].filter(Boolean);
});

const effectiveRequest = computed(() => props.settingsState.llm?.effective_request || {});

const effectiveRequestProviderLabel = computed(() => {
  const request = effectiveRequest.value;
  const provider = request.provider || request.provider_id || props.copy.settings.models.effectiveRequest.noProvider;
  return request.api_mode ? `${provider} (${request.api_mode})` : provider;
});

const effectiveRequestConfiguredLabel = computed(() => (
  effectiveRequest.value.configured
    ? props.copy.settings.models.effectiveRequest.configured
    : props.copy.settings.models.effectiveRequest.notConfigured
));

const effectiveRequestRows = computed(() => {
  const request = effectiveRequest.value;
  const labels = props.copy.settings.models.effectiveRequest.labels;
  const decoding = request.decoding || {};
  const rows = [
    {
      key: "model",
      label: labels.model,
      value: request.model || props.copy.settings.models.noModel,
    },
    {
      key: "decoding",
      label: labels.decoding,
      value: decoding.status === "omitted"
        ? props.copy.settings.models.effectiveRequest.decodingOmitted
        : formatEffectiveParams(decoding.params || {}),
    },
    {
      key: "reasoning",
      label: labels.reasoning,
      value: formatEffectiveReasoning(request.reasoning || {}),
    },
    {
      key: "provider-options",
      label: labels.providerOptions,
      value: formatEffectiveParams(request.provider_options || {}),
    },
  ];
  if (request.context_window_tokens) {
    rows.splice(1, 0, {
      key: "context-window",
      label: labels.contextWindow,
      value: String(request.context_window_tokens),
    });
  }
  return rows;
});

function formatEffectiveReasoning(reasoning) {
  const copyText = props.copy.settings.models.effectiveRequest;
  if (!reasoning.source || reasoning.source === "none") {
    return copyText.reasoningNone;
  }
  if (!reasoning.sent) {
    return `${reasoning.source}: ${copyText.notSent}`;
  }
  return `${reasoning.source}: ${formatEffectiveParams(reasoning.payload || {})}`;
}

function formatEffectiveParams(params) {
  const entries = Object.entries(params || {});
  if (!entries.length) {
    return props.copy.settings.models.effectiveRequest.none;
  }
  return entries.map(([key, value]) => `${key}=${formatEffectiveParamValue(value)}`).join(", ");
}

function formatEffectiveParamValue(value) {
  const copyText = props.copy.settings.models.effectiveRequest;
  if (value === null || value === undefined || value === "") {
    return copyText.omitted;
  }
  if (typeof value === "boolean") {
    return value ? copyText.on : copyText.off;
  }
  if (Array.isArray(value)) {
    return `[${value.map(formatEffectiveParamValue).join(", ")}]`;
  }
  if (typeof value === "object") {
    return `{${formatEffectiveParams(value)}}`;
  }
  return String(value);
}

const showProviderRequestOptions = computed(() => (
  providerSupportsRequestOptions(selectedTextProvider.value) &&
  props.settingsState.providerRequestOptions[selectedTextProvider.value.id]
));

function supportsSelectedProviderRequestOption(option) {
  return providerSupportsRequestOption(selectedTextProvider.value, option);
}

const decodingModeOptions = computed(() => {
  const copyModes = props.copy.settings.models.decodingMode.options;
  const order = Array.isArray(props.settingsState.llm?.decoding_modes)
    ? props.settingsState.llm.decoding_modes
    : ["provider_default", "precise", "balanced", "creative", "custom"];
  return order
    .map((id) => ({ id, ...(copyModes[id] || {}) }))
    .filter((option) => option.label);
});

const selectedDecodingModeDescription = computed(() => {
  const mode = props.settingsState.llm?.decoding_mode || "provider_default";
  return props.copy.settings.models.decodingMode.options[mode]?.description || props.copy.settings.models.decodingMode.description;
});

function handleDecodingModeChange() {
  if (props.settingsState.llm?.decoding_mode !== "custom") {
    emit("save-llm-settings");
  }
}

function hasConnectedProvider(presetId) {
  return props.settingsState.providers.connected.some((provider) => provider.provider === presetId || provider.id === presetId);
}

function providerCredentials(provider) {
  const providerKey = provider?.provider || provider?.id;
  return props.settingsState.credentials?.[providerKey] || [];
}

function providerEffectiveCredentialId(provider) {
  return provider?.credential_effective_id || provider?.credential_id || "";
}

function credentialSourceLabel(provider) {
  const sources = props.copy.settings.providers.credentialSources;
  if (!sources) {
    return "";
  }
  return sources[provider?.credential_source] || "";
}

const showCodexAuthCard = computed(() => (
  hasConnectedProvider("openai-codex") ||
  props.settingsState.codexAuthLoading ||
  props.settingsState.codexAuth.configured ||
  Boolean(props.settingsState.codexAuth.userCode || props.settingsState.codexAuthNotice || props.settingsState.codexAuthError)
));

const showCopilotAuthCard = computed(() => (
  hasConnectedProvider("copilot") ||
  props.settingsState.copilotAuthLoading ||
  props.settingsState.copilotAuth.configured ||
  Boolean(props.settingsState.copilotAuth.userCode || props.settingsState.copilotAuthNotice || props.settingsState.copilotAuthError)
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

const copilotAuthStatusLabel = computed(() => {
  if (props.settingsState.copilotAuthLoading) {
    return props.copy.settings.providers.copilotAuth.loading;
  }
  if (!props.settingsState.copilotAuth.configured) {
    return props.copy.settings.providers.copilotAuth.notConfigured;
  }
  return props.copy.settings.providers.copilotAuth.configured;
});

const copilotAuthDescription = computed(() => {
  const auth = props.settingsState.copilotAuth || {};
  if (!auth.configured) {
    return props.copy.settings.providers.copilotAuth.description;
  }
  return auth.path ? props.copy.settings.providers.copilotAuth.path(auth.path) : props.copy.settings.providers.copilotAuth.configuredDescription;
});

function mediaProviderModels(category) {
  const providerId = props.settingsState.mediaSelections[category]?.providerId;
  return mediaModelsForProvider(category, providerId, props.settingsState.mediaSelections[category]?.model);
}

function mediaProviderModelGroups(category) {
  const providerId = props.settingsState.mediaSelections[category]?.providerId;
  const models = mediaProviderModels(category);
  if (!providerId || !hasSlashModelIds(models)) {
    return [];
  }
  return slashModelGroups(models);
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

function providerDescription(provider) {
  if (provider?.provider === "openai-codex" && !props.settingsState.codexAuth.configured) {
    return props.copy.settings.providers.codexAuth.providerNeedsLogin;
  }
  if (provider?.provider === "copilot" && !props.settingsState.copilotAuth.configured) {
    return props.copy.settings.providers.copilotAuth.providerNeedsLogin;
  }
  return provider?.base_url || "";
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

function webSearchProviderLabel(provider) {
  return props.copy.settings.search.providers?.[provider] || provider;
}

function webSearchFreshnessLabel(freshness) {
  return props.copy.settings.search.freshness.options?.[freshness] || freshness;
}

function toggleSearxngOptions() {
  searxngOptionsExpanded.value = !searxngOptionsExpanded.value;
  if (!searxngOptionsExpanded.value || props.settingsState.searchOptionsLoading) {
    return;
  }
  const options = props.settingsState.search?.searxng_options || {};
  const hasOptions = Boolean(options.engines?.length || options.categories?.length);
  if (!hasOptions) {
    emit("load-search-searxng-options");
  }
}

const webSearchProviderOptions = computed(() => {
  const providers = props.settingsState.search?.providers;
  const values = Array.isArray(providers) && providers.length ? providers : ["duckduckgo", "searxng", "jina"];
  return values.map((id) => ({ id, label: webSearchProviderLabel(id) }));
});

const webSearchFreshnessOptions = computed(() => {
  const freshnessOptions = props.settingsState.search?.freshness_options;
  const values = Array.isArray(freshnessOptions) && freshnessOptions.length ? freshnessOptions : ["auto", "none", "day", "week", "month", "year"];
  return values.map((id) => ({ id, label: webSearchFreshnessLabel(id) }));
});

const webSearchSearxngEngineOptions = computed(() => mergeSelectedSearchOptions(
  props.settingsState.search?.searxng_options?.engines,
  props.settingsState.searchForm?.searxngEngines,
));

const webSearchSearxngCategoryOptions = computed(() => mergeSelectedSearchOptions(
  props.settingsState.search?.searxng_options?.categories,
  props.settingsState.searchForm?.searxngCategories,
));

function mergeSelectedSearchOptions(options = [], selected = []) {
  const merged = new Map();
  for (const option of Array.isArray(options) ? options : []) {
    const id = String(option?.id || "").trim();
    if (!id) continue;
    merged.set(id, {
      ...option,
      id,
      label: String(option.label || id).trim() || id,
      configuredOnly: false,
    });
  }
  for (const id of Array.isArray(selected) ? selected : []) {
    const value = String(id || "").trim();
    if (!value || merged.has(value)) continue;
    merged.set(value, { id: value, label: value, categories: [], shortcut: "", configuredOnly: true });
  }
  return Array.from(merged.values());
}

function searxngEngineMeta(option) {
  const parts = [];
  if (option.shortcut) parts.push(option.shortcut);
  if (Array.isArray(option.categories) && option.categories.length) parts.push(option.categories.join(", "));
  if (option.configuredOnly) parts.push(props.copy.settings.search.searxngOptions.configuredOnly);
  return parts.join(" · ");
}

function webSearchCredentialStatus(provider) {
  const configured = props.settingsState.search?.[`${provider}_api_key_configured`] === true;
  return configured ? props.copy.settings.search.credentials.configured : props.copy.settings.search.credentials.notConfigured;
}

const webSearchSummary = computed(() => {
  const form = props.settingsState.searchForm || {};
  return props.copy.settings.search.summary(
    webSearchProviderLabel(form.provider || "searxng"),
    webSearchFreshnessLabel(form.freshness || "auto"),
    Number(form.maxResults || 25),
  );
});

const browserBackendOptions = computed(() => {
  const backends = props.settingsState.browser?.backends;
  const values = Array.isArray(backends) && backends.length ? backends : ["agent-browser", "browserbase", "browser-use", "firecrawl"];
  return values.map((id) => ({
    id,
    label: props.copy.settings.browser.backends?.[id] || id,
  }));
});

const selectedBrowserBackend = computed(() => props.settingsState.browserForm?.backend || props.settingsState.browser?.backend || "agent-browser");

const selectedBrowserBackendLabel = computed(() => props.copy.settings.browser.backends?.[selectedBrowserBackend.value] || selectedBrowserBackend.value);

const browserRuntimeStatus = computed(() => {
  const runtime = props.settingsState.browser?.runtime || {};
  if (selectedBrowserBackend.value !== "agent-browser") {
    const cloud = props.settingsState.browser?.cloud?.[selectedBrowserBackend.value] || {};
    if (!cloud.configured) {
      return props.copy.settings.browser.cloudMissing(selectedBrowserBackendLabel.value);
    }
    if (!runtime.available) {
      return props.copy.settings.browser.cloudAttachRuntimeMissing(selectedBrowserBackendLabel.value);
    }
    return props.copy.settings.browser.cloudConfigured(selectedBrowserBackendLabel.value);
  }
  if (runtime.available) {
    return props.copy.settings.browser.runtimeAvailable(runtime.command || "agent-browser");
  }
  return props.copy.settings.browser.runtimeMissing;
});

const browserSummary = computed(() => {
  const form = props.settingsState.browserForm || {};
  if (!form.enabled) {
    return props.copy.settings.browser.disabled;
  }
  if (String(form.cdpUrl || "").trim()) {
    return props.copy.settings.browser.cdpEnabled;
  }
  if (selectedBrowserBackend.value !== "agent-browser") {
    return props.copy.settings.browser.cloudEnabled(selectedBrowserBackendLabel.value);
  }
  return props.copy.settings.browser.enabledSummary;
});

const browserTestSummary = computed(() => {
  const result = props.settingsState.browserTestResult;
  if (!result) {
    return props.copy.settings.browser.test.notRun;
  }
  if (result.ok) {
    return props.copy.settings.browser.test.resultPassed(result.url || "");
  }
  return props.copy.settings.browser.test.resultFailed(result.error || result.open?.error || result.snapshot?.error || "");
});

const browserDoctorSummary = computed(() => {
  const result = props.settingsState.browserDoctorResult;
  if (!result) {
    return props.copy.settings.browser.doctor.notRun;
  }
  const checks = Array.isArray(result.checks) ? result.checks : [];
  const passed = checks.filter((check) => check?.ok).length;
  return result.ok
    ? props.copy.settings.browser.doctor.resultPassed(passed, checks.length)
    : props.copy.settings.browser.doctor.resultFailed(passed, checks.length);
});

function browserDoctorCheckSummary(check) {
  const status = check?.ok ? props.copy.settings.browser.doctor.checkPassed : props.copy.settings.browser.doctor.checkFailed;
  const detail = String(check?.suggestion || check?.stderr || check?.stdout || "").trim();
  return detail ? `${status}: ${detail}` : status;
}

const networkSummary = computed(() => {
  const form = props.settingsState.networkForm || {};
  const active = [form.httpProxy, form.httpsProxy].map((value) => String(value || "").trim()).filter(Boolean).length;
  if (!active) {
    return props.copy.settings.network.noProxyConfigured;
  }
  return props.copy.settings.network.proxyConfigured(active);
});

const permissionRiskLevelOptions = computed(() => {
  const options = props.settingsState.permissions?.risk_level_options;
  return Array.isArray(options) && options.length ? options : [];
});

const permissionApprovalModeOptions = computed(() => {
  const options = props.settingsState.permissions?.approval_mode_options;
  return Array.isArray(options) && options.length ? options : [];
});

const permissionSummary = computed(() => {
  const form = props.settingsState.permissionsForm || {};
  const mode = form.approvalMode || props.copy.settings.permissions.inheritMode;
  const required = [
    ...(Array.isArray(form.approvalRequiredRiskLevels) ? form.approvalRequiredRiskLevels : []),
    ...String(form.approvalRequiredTools || "").split(/[\n,]/).map((item) => item.trim()).filter(Boolean),
  ];
  return props.copy.settings.permissions.summary(
    form.enabled !== false,
    props.copy.settings.permissions.approvalModes[mode] || mode,
    required.length,
  );
});

function permissionRiskLabel(riskLevel) {
  return props.copy.settings.permissions.riskLevels?.[riskLevel] || riskLevel;
}

const harnessPolicyPreviewRows = computed(() => {
  const rows = Array.isArray(props.settingsState.harnessPolicyPreview?.rows) ? props.settingsState.harnessPolicyPreview.rows : [];
  const userPermissions = props.settingsState.harnessPolicyPreview?.user_permissions || props.settingsState.permissions || {};
  return rows.map((row) => {
    const profile = row.profile || {};
    const policy = row.policy || {};
    const user = row.user || {};
    const effective = row.effective || {};
    const profileName = profile.name || "unknown";
    const taskType = profile.task_type || "task";
    return {
      key: `${profileName}:${taskType}:${policy.name || "policy"}`,
      profileName,
      title: props.copy.settings.permissions.harnessPreview.rowTitle(profileName, taskType),
      policy: policy.name || "policy",
      description: policy.reason || profile.reason || "",
      harnessAllowed: formatRiskList(policy.allowed_risk_levels),
      userAllowed: formatRiskList(user.allowed_risk_levels || userPermissions.allowed_risk_levels),
      effectiveAllowed: formatRiskList(effective.allowed_risk_levels),
      denied: formatRiskList(effective.denied_risk_levels),
      approval: formatRiskList(effective.approval_required_risk_levels),
      requiredEvidence: formatPreviewList(profile.required_evidence || profile.requiredEvidence),
      verification: profile.verification_policy || profile.verificationPolicy || props.copy.settings.permissions.harnessPreview.none,
      continuation: profile.continuation_policy || profile.continuationPolicy || props.copy.settings.permissions.harnessPreview.none,
    };
  });
});

function formatRiskList(value) {
  const risks = Array.isArray(value) ? value : [];
  if (!risks.length) {
    return props.copy.settings.permissions.harnessPreview.none;
  }
  return risks.map(permissionRiskLabel).join(", ");
}

function formatPreviewList(value) {
  const items = Array.isArray(value) ? value : [];
  if (!items.length) {
    return props.copy.settings.permissions.harnessPreview.none;
  }
  return items.map((item) => String(item || "").trim()).filter(Boolean).join(", ") || props.copy.settings.permissions.harnessPreview.none;
}

const logLevelOptions = computed(() => {
  const levels = props.settingsState.log?.levels;
  return Array.isArray(levels) && levels.length ? levels : ["DEBUG", "INFO", "WARNING", "ERROR"];
});

const logSummary = computed(() => {
  const form = props.settingsState.logForm || {};
  if (!form.enabled) {
    return props.copy.settings.log.disabled;
  }
  return props.copy.settings.log.summary(form.level || "INFO", Number(form.retentionDays || 365));
});

const evalReadinessLabel = computed(() => {
  if (props.settingsState.evalStatus.ready) {
    return props.copy.settings.eval.ready;
  }
  return props.copy.settings.eval.notReady;
});

const evalProcessCounts = computed(() => {
  const counts = props.settingsState.evalStatus.background_process_counts || {};
  const entries = Object.entries(counts).map(([state, count]) => ({ state, count }));
  return entries.length ? entries : [{ state: props.copy.settings.eval.none, count: 0 }];
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

const emit = defineEmits([
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
  "connect-oauth-provider",
  "cancel-provider-connect",
  "save-provider-connection",
  "disconnect-provider",
  "set-provider-credential",
  "delete-credential",
  "refresh-codex-auth",
  "start-codex-auth-login",
  "logout-codex-auth",
  "refresh-copilot-auth",
  "start-copilot-auth-login",
  "logout-copilot-auth",
  "select-model",
  "apply-provider-recommended-options",
  "save-provider-request-options",
  "save-llm-settings",
  "save-log-settings",
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
  "save-network-settings",
  "save-permissions-settings",
  "load-search-searxng-options",
  "save-search-settings",
  "save-browser-settings",
  "run-browser-test",
  "run-browser-doctor",
  "run-browser-install",
  "refresh-eval-status",
  "run-eval-smoke",
  "run-harness-controlled-eval",
  "run-task-completion-smoke",
  "run-task-completion-live",
  "refresh-task-completion-history",
  "delete-task-completion-history-item",
  "clear-task-completion-history",
  "clear-web-sessions",
  "load-data-session-timeline",
  "refresh-curator",
  "run-curator-action",
  "begin-cron-job-create",
  "save-cron-job",
  "edit-cron-job",
  "cancel-cron-job-edit",
  "cron-job-action",
]);
</script>
