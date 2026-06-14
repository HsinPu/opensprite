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
              <label>
                <span>{{ copy.settings.models.reasoningChoice }}</span>
                <select
                  v-model="settingsState.reasoningSelections[selectedTextProvider.id]"
                  :disabled="settingsState.modelsLoading"
                >
                  <option value="">{{ copy.settings.models.reasoningDefault }}</option>
                  <option value="none">{{ copy.settings.models.reasoningNone }}</option>
                  <option value="minimal">{{ copy.settings.models.reasoningMinimal }}</option>
                  <option value="low">{{ copy.settings.models.reasoningLow }}</option>
                  <option value="medium">{{ copy.settings.models.reasoningMedium }}</option>
                  <option value="high">{{ copy.settings.models.reasoningHigh }}</option>
                  <option value="xhigh">{{ copy.settings.models.reasoningXhigh }}</option>
                </select>
              </label>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.modelsLoading || !settingsState.modelSelections[selectedTextProvider.id]"
                @click="$emit(
                  'select-model',
                  selectedTextProvider.id,
                  settingsState.modelSelections[selectedTextProvider.id],
                  settingsState.reasoningSelections[selectedTextProvider.id],
                )"
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
                @click="$emit(
                  'select-model',
                  selectedTextProvider.id,
                  settingsState.customModels[selectedTextProvider.id],
                  settingsState.reasoningSelections[selectedTextProvider.id],
                )"
              >
                {{ copy.settings.models.useCustom }}
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

    </section>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";
import GeneralSettingsPage from "./GeneralSettingsPage.vue";
import SettingsNav from "./SettingsNav.vue";
import ShortcutsSettingsPage from "./ShortcutsSettingsPage.vue";
import { providerSupportsModelMetadata } from "../composables/settingsNormalizers";

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
});

const searxngOptionsExpanded = ref(false);

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
  return formatted ? props.copy.settings.models.modelMetadata.contextLength(formatted) : "";
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
    return { key: "custom", label: props.copy.settings.models.modelMetadata.customGroup };
  }
  const family = normalized.slice(0, separator).trim().toLowerCase();
  return {
    key: family,
    label: modelFamilyLabels[family] || titleCaseModelFamily(family) || props.copy.settings.models.modelMetadata.otherGroup,
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

const selectedTextContextBadge = computed(() => {
  if (!providerSupportsModelMetadata(selectedTextProvider.value, "context_length") || !selectedTextModel.value) {
    return "";
  }
  return modelContextLabel(selectedTextProvider.value, selectedTextModel.value);
});

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
  "load-search-searxng-options",
  "save-search-settings",
  "save-browser-settings",
  "run-browser-test",
  "run-browser-doctor",
  "run-browser-install",
  "clear-web-sessions",
  "begin-cron-job-create",
  "save-cron-job",
  "edit-cron-job",
  "cancel-cron-job-edit",
  "cron-job-action",
]);
</script>
