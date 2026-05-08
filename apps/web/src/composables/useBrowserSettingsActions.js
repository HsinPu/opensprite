const DEFAULT_BROWSER_BACKENDS = ["agent-browser", "browserbase", "browser-use", "firecrawl"];

function normalizeBrowserSettings(browser = {}) {
  return {
    enabled: browser.enabled === true,
    backend: browser.backend || "agent-browser",
    backends: Array.isArray(browser.backends) && browser.backends.length ? browser.backends : DEFAULT_BROWSER_BACKENDS,
    command_timeout: Number(browser.command_timeout || 30),
    session_timeout: Number(browser.session_timeout || 300),
    cdp_url: browser.cdp_url || "",
    allow_private_urls: browser.allow_private_urls === true,
    cloud: browser.cloud || {},
    runtime: browser.runtime || { available: false, command: "", install_hint: "" },
  };
}

function syncBrowserForm(settingsState) {
  settingsState.browserForm.enabled = settingsState.browser.enabled;
  settingsState.browserForm.backend = settingsState.browser.backend;
  settingsState.browserForm.commandTimeout = settingsState.browser.command_timeout;
  settingsState.browserForm.sessionTimeout = settingsState.browser.session_timeout;
  settingsState.browserForm.cdpUrl = settingsState.browser.cdp_url;
  settingsState.browserForm.allowPrivateUrls = settingsState.browser.allow_private_urls;
}

export function useBrowserSettingsActions({ settingsState, requestSettingsJson, copy, setSettingsSuccess }) {
  async function loadBrowserSettings() {
    settingsState.browserLoading = true;
    settingsState.browserError = "";
    try {
      const payload = await requestSettingsJson("/api/settings/browser");
      settingsState.browser = normalizeBrowserSettings(payload.browser || {});
      syncBrowserForm(settingsState);
    } catch (error) {
      settingsState.browserError = error?.message || copy.value.notices.browserLoadFailed;
    } finally {
      settingsState.browserLoading = false;
    }
  }

  async function saveBrowserSettings() {
    settingsState.browserLoading = true;
    settingsState.browserError = "";
    settingsState.browserNotice = "";
    try {
      const payload = await requestSettingsJson("/api/settings/browser", {
        method: "PUT",
        body: JSON.stringify({
          enabled: settingsState.browserForm.enabled,
          backend: settingsState.browserForm.backend,
          command_timeout: settingsState.browserForm.commandTimeout,
          session_timeout: settingsState.browserForm.sessionTimeout,
          cdp_url: settingsState.browserForm.cdpUrl,
          allow_private_urls: settingsState.browserForm.allowPrivateUrls,
        }),
      });
      settingsState.browser = normalizeBrowserSettings(payload.browser || {});
      syncBrowserForm(settingsState);
      setSettingsSuccess("browserNotice", copy.value.notices.browserSaved);
    } catch (error) {
      settingsState.browserError = error?.message || copy.value.notices.browserSaveFailed;
    } finally {
      settingsState.browserLoading = false;
    }
  }

  return {
    loadBrowserSettings,
    saveBrowserSettings,
  };
}
