import { normalizeMcpSettings, normalizeMcpTransport } from "./settingsNormalizers";

function parseLines(value) {
  return String(value || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseListText(value, fallback = []) {
  const items = String(value || "")
    .replace(/,/g, "\n")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length ? items : fallback;
}

function formatJsonObject(value) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? JSON.stringify(value, null, 2)
    : "";
}

function formatListField(value, fallback = "") {
  if (Array.isArray(value)) {
    return value.map((item) => String(item || "").trim()).filter(Boolean).join("\n");
  }
  if (typeof value === "string") {
    return value.trim();
  }
  return fallback;
}

function getMcpServerMap(parsed) {
  if (parsed?.mcpServers && typeof parsed.mcpServers === "object" && !Array.isArray(parsed.mcpServers)) {
    return parsed.mcpServers;
  }
  if (parsed?.mcp_servers && typeof parsed.mcp_servers === "object" && !Array.isArray(parsed.mcp_servers)) {
    return parsed.mcp_servers;
  }
  if (parsed?.servers && typeof parsed.servers === "object" && !Array.isArray(parsed.servers)) {
    return parsed.servers;
  }
  return null;
}

export function useMcpSettingsActions({ settingsState, requestSettingsJson, copy, setSettingsSuccess }) {
  function parseOptionalJsonObject(value, fieldLabel) {
    const text = String(value || "").trim();
    if (!text) {
      return null;
    }
    try {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("not object");
      }
      return parsed;
    } catch {
      settingsState.mcpError = copy.value.notices.mcpJsonInvalid(fieldLabel);
      return undefined;
    }
  }

  function extractMcpServerFromJson(parsed) {
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      settingsState.mcpError = copy.value.notices.mcpJsonInvalid(copy.value.settings.mcp.configJson);
      return null;
    }

    const serverMap = getMcpServerMap(parsed);
    if (serverMap) {
      const entries = Object.entries(serverMap).filter(([, value]) => value && typeof value === "object" && !Array.isArray(value));
      if (entries.length !== 1) {
        settingsState.mcpError = copy.value.notices.mcpJsonSingleServer;
        return null;
      }
      const [serverId, server] = entries[0];
      return { serverId, server };
    }

    if (Array.isArray(parsed.servers)) {
      if (parsed.servers.length !== 1 || !parsed.servers[0] || typeof parsed.servers[0] !== "object") {
        settingsState.mcpError = copy.value.notices.mcpJsonSingleServer;
        return null;
      }
      const server = parsed.servers[0];
      return { serverId: server.id || server.name || server.server_id || server.server_name || "", server };
    }

    if (parsed.server && typeof parsed.server === "object" && !Array.isArray(parsed.server)) {
      return { serverId: parsed.server_name || parsed.server_id || parsed.id || parsed.name || "", server: parsed.server };
    }

    return {
      serverId: parsed.server_id || parsed.serverId || parsed.server_name || parsed.serverName || parsed.id || parsed.name || "",
      server: parsed,
    };
  }

  function resetMcpForm() {
    settingsState.mcpForm.showEditor = false;
    settingsState.mcpForm.editingId = "";
    settingsState.mcpForm.serverId = "";
    settingsState.mcpForm.type = "stdio";
    settingsState.mcpForm.command = "";
    settingsState.mcpForm.argsText = "";
    settingsState.mcpForm.url = "";
    settingsState.mcpForm.envJson = "";
    settingsState.mcpForm.headersJson = "";
    settingsState.mcpForm.toolTimeout = "30";
    settingsState.mcpForm.enabledToolsText = "*";
    settingsState.mcpForm.showAdvanced = false;
    settingsState.mcpForm.showJsonInput = false;
    settingsState.mcpForm.jsonText = "";
  }

  function buildMcpServerPayload() {
    settingsState.mcpError = "";
    const form = settingsState.mcpForm;
    const env = parseOptionalJsonObject(form.envJson, copy.value.settings.mcp.env);
    if (env === undefined) {
      return null;
    }
    const headers = parseOptionalJsonObject(form.headersJson, copy.value.settings.mcp.headers);
    if (headers === undefined) {
      return null;
    }

    const payload = {
      server_id: String(form.serverId || "").trim(),
      type: form.type,
      command: String(form.command || "").trim(),
      args: parseLines(form.argsText),
      url: String(form.url || "").trim(),
      tool_timeout: Number(form.toolTimeout || 30),
      enabled_tools: parseListText(form.enabledToolsText, ["*"]),
    };
    if (env !== null) {
      payload.env = env;
    }
    if (headers !== null) {
      payload.headers = headers;
    }
    return payload;
  }

  async function loadMcpSettings() {
    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    try {
      settingsState.mcp = normalizeMcpSettings(await requestSettingsJson("/api/settings/mcp"), settingsState.mcp.runtime);
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpLoadFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  function beginMcpEdit(server) {
    settingsState.mcpNotice = "";
    settingsState.mcpError = "";
    settingsState.mcpForm.showEditor = true;
    settingsState.mcpForm.editingId = server.id;
    settingsState.mcpForm.serverId = server.id;
    settingsState.mcpForm.type = server.type || "stdio";
    settingsState.mcpForm.command = server.command || "";
    settingsState.mcpForm.argsText = Array.isArray(server.args) ? server.args.join("\n") : "";
    settingsState.mcpForm.url = server.url || "";
    settingsState.mcpForm.envJson = "";
    settingsState.mcpForm.headersJson = "";
    settingsState.mcpForm.toolTimeout = String(server.tool_timeout || 30);
    settingsState.mcpForm.enabledToolsText = Array.isArray(server.enabled_tools) ? server.enabled_tools.join("\n") : "*";
    settingsState.mcpForm.showAdvanced = false;
    settingsState.mcpForm.showJsonInput = false;
    settingsState.mcpForm.jsonText = "";
  }

  function cancelMcpEdit() {
    resetMcpForm();
  }

  function beginMcpCreate() {
    resetMcpForm();
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    settingsState.mcpForm.showEditor = true;
  }

  function toggleMcpAdvanced() {
    settingsState.mcpForm.showAdvanced = !settingsState.mcpForm.showAdvanced;
  }

  function toggleMcpJsonInput() {
    settingsState.mcpForm.showJsonInput = !settingsState.mcpForm.showJsonInput;
  }

  function toggleMcpToolGroup(serverId) {
    const key = String(serverId || "").trim() || "unknown";
    settingsState.mcpToolGroupsExpanded[key] = settingsState.mcpToolGroupsExpanded[key] !== true;
  }

  function applyMcpJson() {
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    let parsed;
    try {
      parsed = JSON.parse(String(settingsState.mcpForm.jsonText || ""));
    } catch {
      settingsState.mcpError = copy.value.notices.mcpJsonInvalid(copy.value.settings.mcp.configJson);
      return;
    }

    const extracted = extractMcpServerFromJson(parsed);
    if (!extracted) {
      return;
    }

    const form = settingsState.mcpForm;
    const server = extracted.server;
    const nextServerId = String(extracted.serverId || "").trim();
    if (form.editingId && nextServerId && nextServerId !== form.editingId) {
      settingsState.mcpError = copy.value.notices.mcpJsonEditingMismatch;
      return;
    }
    if (!form.editingId && nextServerId) {
      form.serverId = nextServerId;
    }

    const rawType = server.type || server.transport_type || server.transport;
    form.type = normalizeMcpTransport(rawType, server.url ? "streamableHttp" : "stdio");
    form.command = String(server.command || "").trim();
    form.argsText = formatListField(server.args, form.argsText);
    form.url = String(server.url || "").trim();
    form.toolTimeout = String(server.tool_timeout || server.toolTimeout || form.toolTimeout || 30);
    form.enabledToolsText = formatListField(server.enabled_tools || server.enabledTools, form.enabledToolsText || "*") || "*";
    form.envJson = formatJsonObject(server.env) || form.envJson;
    form.headersJson = formatJsonObject(server.headers) || form.headersJson;
    form.showAdvanced = Boolean(server.env || server.headers || server.tool_timeout || server.toolTimeout || server.enabled_tools || server.enabledTools);
    form.showJsonInput = false;
    setSettingsSuccess("mcpNotice", copy.value.notices.mcpJsonApplied);
  }

  async function saveMcpServer() {
    const payload = buildMcpServerPayload();
    if (payload === null) {
      return;
    }
    if (!payload.server_id) {
      settingsState.mcpError = copy.value.notices.mcpServerIdRequired;
      return;
    }
    if (payload.type === "stdio" && !payload.command) {
      settingsState.mcpError = copy.value.notices.mcpCommandRequired;
      return;
    }
    if ((payload.type === "sse" || payload.type === "streamableHttp") && !payload.url) {
      settingsState.mcpError = copy.value.notices.mcpUrlRequired;
      return;
    }

    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    try {
      const editingId = settingsState.mcpForm.editingId;
      const response = await requestSettingsJson(editingId ? `/api/settings/mcp/${encodeURIComponent(editingId)}` : "/api/settings/mcp", {
        method: editingId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      settingsState.mcp = normalizeMcpSettings(response, settingsState.mcp.runtime);
      setSettingsSuccess("mcpNotice", response.reload_message || copy.value.notices.mcpSaved);
      resetMcpForm();
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpSaveFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  async function removeMcpServer(server) {
    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    try {
      const response = await requestSettingsJson(`/api/settings/mcp/${encodeURIComponent(server.id)}`, {
        method: "DELETE",
      });
      settingsState.mcp = normalizeMcpSettings(response, settingsState.mcp.runtime);
      setSettingsSuccess("mcpNotice", response.reload_message || copy.value.notices.mcpRemoved);
      if (settingsState.mcpForm.editingId === server.id) {
        resetMcpForm();
      }
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpRemoveFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  async function reloadMcpSettings() {
    settingsState.mcpLoading = true;
    settingsState.mcpError = "";
    settingsState.mcpNotice = "";
    try {
      const response = await requestSettingsJson("/api/settings/mcp/reload", { method: "POST" });
      settingsState.mcp = normalizeMcpSettings(response, settingsState.mcp.runtime);
      setSettingsSuccess("mcpNotice", response.reload_message || copy.value.notices.mcpReloaded);
    } catch (error) {
      settingsState.mcpError = error?.message || copy.value.notices.mcpReloadFailed;
    } finally {
      settingsState.mcpLoading = false;
    }
  }

  return {
    loadMcpSettings,
    beginMcpEdit,
    beginMcpCreate,
    cancelMcpEdit,
    saveMcpServer,
    removeMcpServer,
    reloadMcpSettings,
    toggleMcpAdvanced,
    toggleMcpJsonInput,
    toggleMcpToolGroup,
    applyMcpJson,
  };
}
