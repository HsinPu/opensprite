const MCP_TRANSPORT_TYPES = new Set(["stdio", "sse", "streamableHttp"]);

export function providerModelMetadataFields(provider = {}) {
  return Array.isArray(provider?.model_metadata_fields)
    ? provider.model_metadata_fields.map((field) => String(field || "").trim()).filter(Boolean)
    : [];
}

export function providerSupportsModelMetadata(provider, field) {
  return providerModelMetadataFields(provider).includes(field);
}

export function normalizeMcpTransport(value, fallback = "stdio") {
  const transport = String(value || "").trim();
  if (MCP_TRANSPORT_TYPES.has(transport)) {
    return transport;
  }
  if (["streamable-http", "streamable_http", "http"].includes(transport)) {
    return "streamableHttp";
  }
  return fallback;
}

export function normalizeMcpSettings(payload, fallbackRuntime = {}) {
  return {
    ...payload,
    servers: Array.isArray(payload?.servers) ? payload.servers : [],
    runtime: payload?.runtime && typeof payload.runtime === "object"
      ? {
          connected: Boolean(payload.runtime.connected),
          connecting: Boolean(payload.runtime.connecting),
          connect_failures: Number(payload.runtime.connect_failures || 0),
          retry_after: Number(payload.runtime.retry_after || 0),
          tool_names: Array.isArray(payload.runtime.tool_names) ? payload.runtime.tool_names : [],
        }
      : fallbackRuntime,
  };
}

export function visibleChannels(channels) {
  return (channels || []).filter((channel) => channel.id !== "web" && channel.id !== "console");
}

export function normalizeChannelSettings(payload) {
  const channels = visibleChannels(payload.channels);
  const hasGroupedChannels = Array.isArray(payload.connected) || Array.isArray(payload.available);
  if (hasGroupedChannels) {
    return {
      ...payload,
      connected: visibleChannels(payload.connected),
      available: visibleChannels(payload.available),
      channels,
    };
  }

  return {
    ...payload,
    connected: channels.filter((channel) => channel.token_configured),
    available: channels.filter((channel) => !channel.token_configured),
    channels,
  };
}

export function sortChannelList(channels) {
  return [...channels].sort((left, right) => String(left.name || left.id).localeCompare(String(right.name || right.id)));
}

export function normalizeMediaSettings(payload) {
  const sections = payload?.sections && typeof payload.sections === "object" ? payload.sections : {};
  return {
    ...payload,
    sections: {
      vision: sections.vision || { category: "vision", enabled: false, provider_id: "", model: "" },
      ocr: sections.ocr || { category: "ocr", enabled: false, provider_id: "", model: "" },
      speech: sections.speech || { category: "speech", enabled: false, provider_id: "", model: "" },
      video: sections.video || { category: "video", enabled: false, provider_id: "", model: "" },
    },
    providers: Array.isArray(payload?.providers) ? payload.providers : [],
  };
}
