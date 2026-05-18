export const DEFAULT_BROWSER_BACKEND = "agent-browser";
export const DEFAULT_BROWSER_BACKENDS = ["agent-browser", "browserbase", "browser-use", "firecrawl"];
export const DEFAULT_BROWSER_COMMAND_TIMEOUT = 30;
export const DEFAULT_BROWSER_SESSION_TIMEOUT = 1800;
export const DEFAULT_BROWSER_LAUNCH_ARGS = "--no-sandbox";
export const DEFAULT_BROWSER_TEST_URL = "https://quotes.toscrape.com/js/";

export function createDefaultBrowserState() {
  return {
    enabled: false,
    backend: DEFAULT_BROWSER_BACKEND,
    backends: DEFAULT_BROWSER_BACKENDS,
    command_timeout: DEFAULT_BROWSER_COMMAND_TIMEOUT,
    session_timeout: DEFAULT_BROWSER_SESSION_TIMEOUT,
    cdp_url: "",
    launch_args: DEFAULT_BROWSER_LAUNCH_ARGS,
    allow_private_urls: false,
    cloud: {},
    runtime: {
      available: false,
      command: "",
      install_hint: "",
    },
  };
}

export function createDefaultBrowserForm() {
  return {
    enabled: false,
    backend: DEFAULT_BROWSER_BACKEND,
    commandTimeout: DEFAULT_BROWSER_COMMAND_TIMEOUT,
    sessionTimeout: DEFAULT_BROWSER_SESSION_TIMEOUT,
    cdpUrl: "",
    launchArgs: DEFAULT_BROWSER_LAUNCH_ARGS,
    allowPrivateUrls: false,
    testUrl: DEFAULT_BROWSER_TEST_URL,
  };
}

export function normalizeBrowserSettings(browser = {}) {
  return {
    ...createDefaultBrowserState(),
    enabled: browser.enabled === true,
    backend: browser.backend || DEFAULT_BROWSER_BACKEND,
    backends: Array.isArray(browser.backends) && browser.backends.length ? browser.backends : DEFAULT_BROWSER_BACKENDS,
    command_timeout: Number(browser.command_timeout || DEFAULT_BROWSER_COMMAND_TIMEOUT),
    session_timeout: Number(browser.session_timeout || DEFAULT_BROWSER_SESSION_TIMEOUT),
    cdp_url: browser.cdp_url || "",
    launch_args: browser.launch_args || DEFAULT_BROWSER_LAUNCH_ARGS,
    allow_private_urls: browser.allow_private_urls === true,
    cloud: browser.cloud || {},
    runtime: browser.runtime || { available: false, command: "", install_hint: "" },
  };
}
