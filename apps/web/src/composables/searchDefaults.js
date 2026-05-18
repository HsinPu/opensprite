export const DEFAULT_SEARCH_PROVIDER = "duckduckgo";
export const DEFAULT_SEARCH_PROVIDERS = ["duckduckgo", "brave", "tavily", "searxng", "jina"];
export const DEFAULT_SEARCH_FRESHNESS = "year";
export const DEFAULT_FRESHNESS_OPTIONS = ["none", "day", "week", "month", "year"];
export const DEFAULT_SEARXNG_URL = "https://searx.be";
export const DEFAULT_SEARCH_MAX_RESULTS = 25;
export const DEFAULT_DUCKDUCKGO_MAX_PAGES = 10;
export const DEFAULT_SEARXNG_MAX_PAGES = 5;

export function createDefaultSearchState() {
  return {
    provider: DEFAULT_SEARCH_PROVIDER,
    providers: [...DEFAULT_SEARCH_PROVIDERS],
    freshness: DEFAULT_SEARCH_FRESHNESS,
    freshness_options: [...DEFAULT_FRESHNESS_OPTIONS],
    max_results: DEFAULT_SEARCH_MAX_RESULTS,
    duckduckgo_max_pages: DEFAULT_DUCKDUCKGO_MAX_PAGES,
    searxng_max_pages: DEFAULT_SEARXNG_MAX_PAGES,
    searxng_url: DEFAULT_SEARXNG_URL,
    searxng_engines: [],
    searxng_categories: [],
    searxng_options: {
      engines: [],
      categories: [],
    },
    proxy: "",
    brave_api_key_configured: false,
    tavily_api_key_configured: false,
    jina_api_key_configured: false,
  };
}

export function createDefaultSearchForm() {
  return {
    provider: DEFAULT_SEARCH_PROVIDER,
    freshness: DEFAULT_SEARCH_FRESHNESS,
    maxResults: DEFAULT_SEARCH_MAX_RESULTS,
    duckduckgoMaxPages: DEFAULT_DUCKDUCKGO_MAX_PAGES,
    searxngMaxPages: DEFAULT_SEARXNG_MAX_PAGES,
    searxngUrl: DEFAULT_SEARXNG_URL,
    searxngEngines: [],
    searxngCategories: [],
    proxy: "",
    braveApiKey: "",
    tavilyApiKey: "",
    jinaApiKey: "",
  };
}
