import { normalizeChannelSettings, sortChannelList, visibleChannels } from "./settingsNormalizers";

export function useChannelSettingsActions({ settingsState, requestSettingsJson, copy, setSettingsSuccess, cancelProviderConnect }) {
  function upsertConnectedChannel(channel) {
    const visibleChannel = visibleChannels([channel])[0];
    if (!visibleChannel) {
      return;
    }
    const connected = settingsState.channels.connected.filter((entry) => entry.id !== visibleChannel.id);
    const nextConnected = sortChannelList([...connected, visibleChannel]);
    settingsState.channels = {
      ...settingsState.channels,
      connected: nextConnected,
      channels: nextConnected,
    };
  }

  function removeConnectedChannel(channelId) {
    const nextConnected = settingsState.channels.connected.filter((entry) => entry.id !== channelId);
    settingsState.channels = {
      ...settingsState.channels,
      connected: nextConnected,
      channels: nextConnected,
    };
  }

  async function loadChannelSettings() {
    settingsState.channelsLoading = true;
    settingsState.channelsError = "";
    try {
      const payload = await requestSettingsJson("/api/settings/channels");
      settingsState.channels = normalizeChannelSettings(payload);
    } catch (error) {
      settingsState.channelsError = error?.message || copy.value.notices.channelLoadFailed;
    } finally {
      settingsState.channelsLoading = false;
    }
  }

  function beginChannelConnect(channel) {
    settingsState.channelsNotice = "";
    settingsState.channelsError = "";
    cancelProviderConnect?.();
    settingsState.channelConnectForm.type = channel.type || channel.id;
    settingsState.channelConnectForm.name = channel.name || "";
    settingsState.channelConnectForm.token = "";
  }

  function cancelChannelConnect() {
    settingsState.channelConnectForm.type = "";
    settingsState.channelConnectForm.name = "";
    settingsState.channelConnectForm.token = "";
  }

  async function saveChannelConnection() {
    const channelType = settingsState.channelConnectForm.type;
    if (!channelType) {
      return;
    }
    settingsState.channelsLoading = true;
    settingsState.channelsError = "";
    settingsState.channelsNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/channels", {
        method: "POST",
        body: JSON.stringify({
          type: channelType,
          name: settingsState.channelConnectForm.name,
          token: settingsState.channelConnectForm.token,
        }),
      });
      setSettingsSuccess("channelsNotice", copy.value.notices.channelConnected(payload.channel.name, payload.restart_required));
      upsertConnectedChannel(payload.channel);
      cancelChannelConnect();
      await loadChannelSettings();
    } catch (error) {
      settingsState.channelsError = error?.message || copy.value.notices.channelConnectFailed;
    } finally {
      settingsState.channelsLoading = false;
    }
  }

  async function disconnectChannel(channel) {
    settingsState.channelsLoading = true;
    settingsState.channelsError = "";
    settingsState.channelsNotice = "";
    try {
      const payload = await requestSettingsJson(`/api/settings/channels/${encodeURIComponent(channel.id)}/disconnect`, {
        method: "POST",
      });
      setSettingsSuccess("channelsNotice", copy.value.notices.channelDisconnected(channel.name, payload.restart_required));
      removeConnectedChannel(channel.id);
      await loadChannelSettings();
    } catch (error) {
      settingsState.channelsError = error?.message || copy.value.notices.channelDisconnectFailed;
    } finally {
      settingsState.channelsLoading = false;
    }
  }

  return {
    loadChannelSettings,
    beginChannelConnect,
    cancelChannelConnect,
    saveChannelConnection,
    disconnectChannel,
  };
}
