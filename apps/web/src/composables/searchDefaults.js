export const DEFAULT_SEARCH_PROVIDER = "duckduckgo";
export const DEFAULT_SEARCH_PROVIDERS = ["duckduckgo", "brave", "tavily", "searxng", "jina"];
export const DEFAULT_SEARCH_FRESHNESS = "year";
export const DEFAULT_FRESHNESS_OPTIONS = ["none", "day", "week", "month", "year"];
export const DEFAULT_SEARXNG_URL = "https://searx.be";

export function createDefaultSearchState() {
  return {
    provider: DEFAULT_SEARCH_PROVIDER,
    providers: [...DEFAULT_SEARCH_PROVIDERS],
    freshness: DEFAULT_SEARCH_FRESHNESS,
    freshness_options: [...DEFAULT_FRESHNESS_OPTIONS],
    max_results: 25,
    duckduckgo_max_pages: 10,
    searxng_max_pages: 5,
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
    maxResults: 25,
    duckduckgoMaxPages: 10,
    searxngMaxPages: 5,
    searxngUrl: DEFAULT_SEARXNG_URL,
    searxngEngines: [],
    searxngCategories: [],
    proxy: "",
    braveApiKey: "",
    tavilyApiKey: "",
    jinaApiKey: "",
  };
}
