import { normalizeBrowserSettings } from "./browserDefaults";

function syncBrowserForm(settingsState) {
  settingsState.browserForm.enabled = settingsState.browser.enabled;
  settingsState.browserForm.backend = settingsState.browser.backend;
  settingsState.browserForm.commandTimeout = settingsState.browser.command_timeout;
  settingsState.browserForm.sessionTimeout = settingsState.browser.session_timeout;
  settingsState.browserForm.cdpUrl = settingsState.browser.cdp_url;
  settingsState.browserForm.launchArgs = settingsState.browser.launch_args;
  settingsState.browserForm.allowPrivateUrls = settingsState.browser.allow_private_urls;
}

function summarizeBrowserTest(payload, copy) {
  const browserCopy = copy.value.settings.browser;
  if (payload?.ok) {
    return browserCopy.testPassed(payload.url || "");
  }
  return browserCopy.testFailed(payload?.suggestion || payload?.error || payload?.open?.suggestion || payload?.open?.error || payload?.snapshot?.suggestion || payload?.snapshot?.error || "");
}

function summarizeBrowserDoctor(payload, copy) {
  const browserCopy = copy.value.settings.browser;
  const checks = Array.isArray(payload?.checks) ? payload.checks : [];
  const passed = checks.filter((check) => check?.ok).length;
  return payload?.ok ? browserCopy.doctorPassed(passed, checks.length) : browserCopy.doctorFailed(passed, checks.length);
}

function summarizeBrowserInstall(payload, copy) {
  const browserCopy = copy.value.settings.browser;
  if (payload?.already_installed) {
    return browserCopy.installAlreadyInstalled;
  }
  return payload?.ok ? browserCopy.installPassed : browserCopy.installFailed(payload?.after?.suggestion || payload?.install?.suggestion || "");
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
          launch_args: settingsState.browserForm.launchArgs,
          allow_private_urls: settingsState.browserForm.allowPrivateUrls,
        }),
      });
      settingsState.browser = normalizeBrowserSettings(payload.browser || {});
      syncBrowserForm(settingsState);
      setSettingsSuccess(
        "browserNotice",
        payload.restart_required ? copy.value.notices.browserRestartRequired : copy.value.notices.browserSaved,
      );
    } catch (error) {
      settingsState.browserError = error?.message || copy.value.notices.browserSaveFailed;
    } finally {
      settingsState.browserLoading = false;
    }
  }

  async function runBrowserTest() {
    settingsState.browserTestLoading = true;
    settingsState.browserError = "";
    settingsState.browserNotice = "";
    settingsState.browserTestResult = null;
    try {
      const payload = await requestSettingsJson("/api/settings/browser/test", {
        method: "POST",
        body: JSON.stringify({
          url: settingsState.browserForm.testUrl,
        }),
      });
      settingsState.browser = normalizeBrowserSettings(payload.browser || settingsState.browser || {});
      settingsState.browserTestResult = payload;
      settingsState.browserNotice = summarizeBrowserTest(payload, copy);
    } catch (error) {
      settingsState.browserError = error?.message || copy.value.notices.browserTestFailed;
    } finally {
      settingsState.browserTestLoading = false;
    }
  }

  async function runBrowserDoctor() {
    settingsState.browserDoctorLoading = true;
    settingsState.browserError = "";
    settingsState.browserNotice = "";
    settingsState.browserDoctorResult = null;
    try {
      const payload = await requestSettingsJson("/api/settings/browser/doctor", { method: "POST" });
      settingsState.browser = normalizeBrowserSettings(payload.browser || settingsState.browser || {});
      settingsState.browserDoctorResult = payload;
      settingsState.browserNotice = summarizeBrowserDoctor(payload, copy);
    } catch (error) {
      settingsState.browserError = error?.message || copy.value.notices.browserDoctorFailed;
    } finally {
      settingsState.browserDoctorLoading = false;
    }
  }

  async function runBrowserInstall() {
    settingsState.browserInstallLoading = true;
    settingsState.browserError = "";
    settingsState.browserNotice = "";
    settingsState.browserInstallResult = null;
    try {
      const payload = await requestSettingsJson("/api/settings/browser/install", { method: "POST" });
      settingsState.browser = normalizeBrowserSettings(payload.browser || settingsState.browser || {});
      settingsState.browserInstallResult = payload;
      settingsState.browserDoctorResult = payload.after ? { ok: payload.ok, browser: payload.browser, runtime: payload.runtime, checks: [payload.after] } : settingsState.browserDoctorResult;
      settingsState.browserNotice = summarizeBrowserInstall(payload, copy);
    } catch (error) {
      settingsState.browserError = error?.message || copy.value.notices.browserInstallFailed;
    } finally {
      settingsState.browserInstallLoading = false;
    }
  }

  return {
    loadBrowserSettings,
    saveBrowserSettings,
    runBrowserTest,
    runBrowserDoctor,
    runBrowserInstall,
  };
}
