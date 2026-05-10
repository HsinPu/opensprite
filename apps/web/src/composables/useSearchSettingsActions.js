const DEFAULT_SEARCH_PROVIDERS = ["duckduckgo", "brave", "tavily", "searxng", "jina"];
const DEFAULT_FRESHNESS_OPTIONS = ["none", "day", "week", "month", "year"];

function normalizeTextList(value) {
  const values = Array.isArray(value) ? value : String(value || "").split(/[\n,]+/);
  return values.map((item) => String(item || "").trim()).filter(Boolean);
}

function formatTextList(value) {
  return normalizeTextList(value).join(", ");
}

function normalizeSearchSettings(search = {}) {
  return {
    provider: search.provider || "searxng",
    providers: Array.isArray(search.providers) && search.providers.length ? search.providers : DEFAULT_SEARCH_PROVIDERS,
    freshness: search.freshness || "year",
    freshness_options: Array.isArray(search.freshness_options) && search.freshness_options.length ? search.freshness_options : DEFAULT_FRESHNESS_OPTIONS,
    max_results: Number(search.max_results || 25),
    duckduckgo_max_pages: Number(search.duckduckgo_max_pages || 10),
    searxng_max_pages: Number(search.searxng_max_pages || 5),
    searxng_url: search.searxng_url || "https://searx.be",
    searxng_engines: normalizeTextList(search.searxng_engines),
    searxng_categories: normalizeTextList(search.searxng_categories),
    proxy: search.proxy || "",
    brave_api_key_configured: search.brave_api_key_configured === true,
    tavily_api_key_configured: search.tavily_api_key_configured === true,
    jina_api_key_configured: search.jina_api_key_configured === true,
  };
}

function syncSearchForm(settingsState) {
  settingsState.searchForm.provider = settingsState.search.provider;
  settingsState.searchForm.freshness = settingsState.search.freshness;
  settingsState.searchForm.maxResults = settingsState.search.max_results;
  settingsState.searchForm.duckduckgoMaxPages = settingsState.search.duckduckgo_max_pages;
  settingsState.searchForm.searxngMaxPages = settingsState.search.searxng_max_pages;
  settingsState.searchForm.searxngUrl = settingsState.search.searxng_url;
  settingsState.searchForm.searxngEngines = formatTextList(settingsState.search.searxng_engines);
  settingsState.searchForm.searxngCategories = formatTextList(settingsState.search.searxng_categories);
  settingsState.searchForm.proxy = settingsState.search.proxy;
  settingsState.searchForm.braveApiKey = "";
  settingsState.searchForm.tavilyApiKey = "";
  settingsState.searchForm.jinaApiKey = "";
}

function secretPayload(form) {
  const payload = {};
  const braveApiKey = String(form.braveApiKey || "").trim();
  const tavilyApiKey = String(form.tavilyApiKey || "").trim();
  const jinaApiKey = String(form.jinaApiKey || "").trim();
  if (braveApiKey) payload.brave_api_key = braveApiKey;
  if (tavilyApiKey) payload.tavily_api_key = tavilyApiKey;
  if (jinaApiKey) payload.jina_api_key = jinaApiKey;
  return payload;
}

export function useSearchSettingsActions({ settingsState, requestSettingsJson, copy, setSettingsSuccess }) {
  async function loadSearchSettings() {
    settingsState.searchLoading = true;
    settingsState.searchError = "";
    try {
      const payload = await requestSettingsJson("/api/settings/search");
      settingsState.search = normalizeSearchSettings(payload.search || {});
      syncSearchForm(settingsState);
    } catch (error) {
      settingsState.searchError = error?.message || copy.value.notices.searchLoadFailed;
    } finally {
      settingsState.searchLoading = false;
    }
  }

  async function saveSearchSettings() {
    settingsState.searchLoading = true;
    settingsState.searchError = "";
    settingsState.searchNotice = "";
    try {
      const form = settingsState.searchForm;
      const payload = await requestSettingsJson("/api/settings/search", {
        method: "PUT",
        body: JSON.stringify({
          provider: form.provider,
          freshness: form.freshness,
          max_results: form.maxResults,
          duckduckgo_max_pages: form.duckduckgoMaxPages,
          searxng_max_pages: form.searxngMaxPages,
          searxng_url: form.searxngUrl,
          searxng_engines: normalizeTextList(form.searxngEngines),
          searxng_categories: normalizeTextList(form.searxngCategories),
          proxy: form.proxy,
          ...secretPayload(form),
        }),
      });
      settingsState.search = normalizeSearchSettings(payload.search || {});
      syncSearchForm(settingsState);
      setSettingsSuccess("searchNotice", copy.value.notices.searchSaved);
    } catch (error) {
      settingsState.searchError = error?.message || copy.value.notices.searchSaveFailed;
    } finally {
      settingsState.searchLoading = false;
    }
  }

  return {
    loadSearchSettings,
    saveSearchSettings,
  };
}
