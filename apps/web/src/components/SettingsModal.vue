<template>
  <div v-if="open" class="settings-modal">
    <button
      class="settings-modal__backdrop"
      type="button"
      aria-label="Close settings"
      @click="$emit('close')"
    ></button>

    <section class="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
      <aside class="settings-nav" aria-label="Settings sections">
        <div class="settings-nav__group">
          <p>桌面</p>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'general' }"
            type="button"
            @click="$emit('select-section', 'general')"
          >
            <span aria-hidden="true">⌘</span>
            一般
          </button>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'shortcuts' }"
            type="button"
            @click="$emit('select-section', 'shortcuts')"
          >
            <span aria-hidden="true">⌗</span>
            快速鍵
          </button>
        </div>

        <div class="settings-nav__group">
          <p>伺服器</p>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'providers' }"
            type="button"
            @click="$emit('select-section', 'providers')"
          >
            <span aria-hidden="true">⚙</span>
            提供者
          </button>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'models' }"
            type="button"
            @click="$emit('select-section', 'models')"
          >
            <span aria-hidden="true">✦</span>
            模型
          </button>
          <button
            class="settings-nav__item"
            :class="{ 'settings-nav__item--active': section === 'channels' }"
            type="button"
            @click="$emit('select-section', 'channels')"
          >
            <span aria-hidden="true">☷</span>
            頻道
          </button>
        </div>

        <div class="settings-nav__footer">
          <strong>OpenSprite Web</strong>
          <span>v0.1.0</span>
        </div>
      </aside>

      <div class="settings-content">
        <header class="settings-content__header">
          <h2 id="settingsTitle">{{ title }}</h2>
          <button class="settings-panel__close" type="button" aria-label="Close settings" @click="$emit('close')">
            Close
          </button>
        </header>

        <section v-show="section === 'general'" class="settings-page">
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>語言</strong>
                <span>變更 OpenSprite 的顯示語言</span>
              </div>
              <select aria-label="Language">
                <option>繁體中文</option>
                <option>English</option>
              </select>
            </div>

            <label class="settings-row">
              <div>
                <strong>顯示 Run 進度</strong>
                <span>在對話下方顯示目前執行狀態摘要。</span>
              </div>
              <input v-model="form.showRunTimeline" class="switch" type="checkbox" />
            </label>

            <label class="settings-row">
              <div>
                <strong>顯示 Trace</strong>
                <span>顯示工具、LLM、驗證事件等詳細追蹤資訊。</span>
              </div>
              <input v-model="form.showRunTrace" class="switch" type="checkbox" />
            </label>
          </div>

          <h3>連線</h3>
          <div class="settings-card settings-card--form">
            <label class="settings-row settings-row--field">
              <div>
                <strong>WebSocket URL</strong>
                <span>OpenSprite gateway 的連線位置</span>
              </div>
              <input v-model="form.wsUrl" type="text" spellcheck="false" />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>Display name</strong>
                <span>送出訊息時顯示的使用者名稱</span>
              </div>
              <input v-model="form.displayName" type="text" maxlength="60" />
            </label>

            <label class="settings-row settings-row--field">
              <div>
                <strong>External chat ID</strong>
                <span>用固定外部 ID 將瀏覽器分頁綁定到同一個 session</span>
              </div>
              <input v-model="form.externalChatId" type="text" spellcheck="false" />
            </label>

            <label class="settings-row">
              <div>
                <strong>Gateway 連線</strong>
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

          <h3>外觀</h3>
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>配色方案</strong>
                <span>選擇 OpenSprite 要跟隨系統、淺色或深色主題</span>
              </div>
              <select aria-label="Color scheme">
                <option>系統</option>
                <option>淺色</option>
                <option>深色</option>
              </select>
            </div>
          </div>
        </section>

        <section v-show="section === 'shortcuts'" class="settings-page">
          <div class="settings-card">
            <div class="settings-row">
              <div>
                <strong>開啟設定</strong>
                <span>快速開啟這個設定視窗</span>
              </div>
              <div class="shortcut-keys"><kbd>Ctrl</kbd><kbd>,</kbd></div>
            </div>
            <div class="settings-row">
              <div>
                <strong>送出訊息</strong>
                <span>在輸入框中送出目前訊息</span>
              </div>
              <div class="shortcut-keys"><kbd>Enter</kbd></div>
            </div>
          </div>
        </section>

        <section v-show="section === 'channels'" class="settings-page">
          <p v-if="settingsState.channelsLoading" class="settings-inline-status">讀取頻道設定中...</p>
          <p v-if="settingsState.channelsError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.channelsError }}
          </p>
          <p v-if="settingsState.channelsNotice" class="settings-inline-status settings-inline-status--success">
            {{ settingsState.channelsNotice }}
          </p>

          <h3>已連線的頻道</h3>
          <div class="settings-card provider-card">
            <div v-if="settingsState.channels.connected.length === 0" class="provider-row provider-row--empty">
              <div>
                <strong>尚未連線頻道</strong>
                <span>從下方可新增頻道開始連線。</span>
              </div>
            </div>

            <div v-for="channel in settingsState.channels.connected" :key="channel.id" class="provider-row">
              <div class="provider-row__main">
                <span class="provider-row__mark" aria-hidden="true">{{ channel.name.slice(0, 2) }}</span>
                <div>
                  <div class="provider-row__title">
                    <strong>{{ channel.name }}</strong>
                    <span class="provider-row__badge">已連線</span>
                    <span v-if="channel.enabled" class="provider-row__badge">啟用</span>
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
                中斷連線
              </button>
            </div>
          </div>

          <h3>可新增頻道</h3>
          <div class="settings-card provider-card">
            <div v-if="settingsState.channels.available.length === 0" class="provider-row provider-row--empty">
              <div>
                <strong>所有內建頻道都已連線</strong>
                <span>需要停用時可在上方中斷連線。</span>
              </div>
            </div>

            <div v-for="channel in settingsState.channels.available" :key="channel.id" class="provider-row provider-row--stacked">
              <div class="provider-row__content">
                <div class="provider-row__main">
                  <span class="provider-row__mark" aria-hidden="true">{{ channel.name.slice(0, 2) }}</span>
                  <div>
                    <div class="provider-row__title">
                      <strong>{{ channel.name }}</strong>
                      <span class="provider-row__badge">內建</span>
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
                  + 新增
                </button>
              </div>
            </div>
          </div>
        </section>

        <section v-show="section === 'providers'" class="settings-page">
          <p v-if="settingsState.providersLoading" class="settings-inline-status">讀取提供商設定中...</p>
          <p v-if="settingsState.providersError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.providersError }}
          </p>
          <p v-if="settingsState.providersNotice" class="settings-inline-status settings-inline-status--success">
            {{ settingsState.providersNotice }}
          </p>

          <h3>已連線的提供商</h3>
          <div class="settings-card provider-card">
            <div v-if="settingsState.providers.connected.length === 0" class="provider-row provider-row--empty">
              <div>
                <strong>尚未連線提供商</strong>
                <span>從下方熱門提供商開始連線。</span>
              </div>
            </div>

            <div v-for="provider in settingsState.providers.connected" :key="provider.id" class="provider-row">
              <div class="provider-row__main">
                <span class="provider-row__mark" aria-hidden="true">{{ provider.name.slice(0, 2) }}</span>
                <div>
                  <div class="provider-row__title">
                    <strong>{{ provider.name }}</strong>
                    <span v-if="provider.is_default" class="provider-row__badge">目前使用中</span>
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
                中斷連線
              </button>
            </div>
          </div>

          <h3>熱門提供商</h3>
          <div class="settings-card provider-card">
            <div v-if="settingsState.providers.available.length === 0" class="provider-row provider-row--empty">
              <div>
                <strong>所有內建提供商都已連線</strong>
                <span>請到模型頁選擇要使用的模型。</span>
              </div>
            </div>

            <div v-for="provider in settingsState.providers.available" :key="provider.id" class="provider-row provider-row--stacked">
              <div class="provider-row__content">
                <div class="provider-row__main">
                  <span class="provider-row__mark" aria-hidden="true">{{ provider.name.slice(0, 2) }}</span>
                  <div>
                    <div class="provider-row__title">
                      <strong>{{ provider.name }}</strong>
                      <span class="provider-row__badge">內建</span>
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
                  + 連線
                </button>
              </div>

            </div>
          </div>
        </section>

        <section v-show="section === 'models'" class="settings-page">
          <p v-if="settingsState.modelsLoading" class="settings-inline-status">讀取模型設定中...</p>
          <p v-if="settingsState.modelsError" class="settings-inline-status settings-inline-status--error">
            {{ settingsState.modelsError }}
          </p>
          <p v-if="settingsState.modelsNotice" class="settings-inline-status settings-inline-status--success">
            {{ settingsState.modelsNotice }}
          </p>

          <div v-if="settingsState.models.providers.length === 0" class="settings-card">
            <div class="settings-row">
              <div>
                <strong>尚未連線提供商</strong>
                <span>請先到提供者頁連線 OpenAI、OpenRouter 或 MiniMax。</span>
              </div>
              <span class="settings-muted">No providers</span>
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
                    <span v-if="provider.is_default" class="provider-row__badge">目前使用中</span>
                  </div>
                  <span>{{ provider.selected_model || "尚未選擇模型" }}</span>
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
                <span>{{ provider.is_default && provider.selected_model === model ? "目前使用中" : "選擇模型" }}</span>
              </button>
            </div>

            <div class="custom-model-row">
              <label>
                <span>自訂模型</span>
                <input
                  v-model="settingsState.customModels[provider.id]"
                  type="text"
                  placeholder="輸入模型名稱"
                  spellcheck="false"
                />
              </label>
              <button
                class="secondary-button"
                type="button"
                :disabled="settingsState.modelsLoading"
                @click="$emit('select-model', provider.id, settingsState.customModels[provider.id])"
              >
                使用自訂模型
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
            aria-label="Back to providers"
            @click="$emit('cancel-provider-connect')"
          >
            ←
          </button>
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            aria-label="Close provider connection"
            @click="$emit('cancel-provider-connect')"
          >
            ×
          </button>
        </header>

        <form class="provider-connect-dialog__body" @submit.prevent="$emit('save-provider-connection')">
          <div class="provider-connect-dialog__title">
            <span class="provider-row__mark" aria-hidden="true">{{ selectedConnectProvider.name.slice(0, 2) }}</span>
            <h3>連線 {{ selectedConnectProvider.name }}</h3>
          </div>

          <p>
            輸入你的 {{ selectedConnectProvider.name }} API key 以連線帳戶，之後可到模型頁選擇使用的模型。
          </p>

          <label class="provider-connect-field">
            <span>{{ selectedConnectProvider.name }} API key</span>
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
            {{ settingsState.connectForm.showAdvanced ? "隱藏進階設定" : "進階設定" }}
          </button>

          <label v-if="settingsState.connectForm.showAdvanced" class="provider-connect-field">
            <span>Base URL</span>
            <input v-model="settingsState.connectForm.baseUrl" type="text" spellcheck="false" />
          </label>

          <button class="primary-button provider-connect-dialog__submit" type="submit">
            提交
          </button>
        </form>
      </div>

      <div v-if="selectedConnectChannel" class="provider-connect-dialog" role="dialog" aria-modal="true">
        <header class="provider-connect-dialog__top">
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            aria-label="Back to channels"
            @click="$emit('cancel-channel-connect')"
          >
            ←
          </button>
          <button
            class="provider-connect-dialog__icon-button"
            type="button"
            aria-label="Close channel connection"
            @click="$emit('cancel-channel-connect')"
          >
            ×
          </button>
        </header>

        <form class="provider-connect-dialog__body" @submit.prevent="$emit('save-channel-connection')">
          <div class="provider-connect-dialog__title">
            <span class="provider-row__mark" aria-hidden="true">{{ selectedConnectChannel.name.slice(0, 2) }}</span>
            <h3>新增 {{ selectedConnectChannel.name }}</h3>
          </div>

          <p>輸入你的 {{ selectedConnectChannel.name }} token 以連線這個頻道。</p>

          <label class="provider-connect-field">
            <span>頻道名稱</span>
            <input
              v-model="settingsState.channelConnectForm.name"
              type="text"
              placeholder="例如：工作 Telegram"
              autocomplete="off"
            />
          </label>

          <label class="provider-connect-field">
            <span>{{ selectedConnectChannel.name }} token</span>
            <input
              v-model="settingsState.channelConnectForm.token"
              type="password"
              placeholder="Token"
              autocomplete="off"
            />
          </label>

          <button class="primary-button provider-connect-dialog__submit" type="submit">
            提交
          </button>
        </form>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
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
    return "正在連線到 OpenSprite gateway...";
  }
  if (props.connectionState === "connected") {
    return "已連線。關掉開關後會中斷目前 gateway 連線。";
  }
  return "開啟後會套用上方連線設定並連線。";
});

defineEmits([
  "close",
  "select-section",
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
