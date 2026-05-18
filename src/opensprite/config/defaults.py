"""Shared defaults used by config, settings APIs, and tool fallbacks."""

from __future__ import annotations

DEFAULT_WEB_SEARCH_PROVIDER = "duckduckgo"
WEB_SEARCH_PROVIDERS = ("duckduckgo", "brave", "tavily", "searxng", "jina")
DEFAULT_WEB_SEARCH_FRESHNESS = "year"
WEB_SEARCH_FRESHNESS_OPTIONS = ("none", "day", "week", "month", "year")
DEFAULT_SEARXNG_URL = "https://searx.be"
DEFAULT_WEB_SEARCH_MAX_RESULTS = 25
DEFAULT_DUCKDUCKGO_MAX_PAGES = 10
DEFAULT_SEARXNG_MAX_PAGES = 5

DEFAULT_BROWSER_BACKEND = "agent-browser"
BROWSER_BACKENDS = ("agent-browser", "browserbase", "browser-use", "firecrawl")
CLOUD_BROWSER_BACKENDS = ("browserbase", "browser-use", "firecrawl")
DEFAULT_BROWSER_COMMAND_TIMEOUT = 30
DEFAULT_BROWSER_SESSION_TIMEOUT = 1800
DEFAULT_BROWSER_LAUNCH_ARGS = "--no-sandbox"
DEFAULT_BROWSERBASE_BASE_URL = "https://api.browserbase.com"
DEFAULT_BROWSER_USE_BASE_URL = "https://api.browser-use.com/api/v3"
DEFAULT_FIRECRAWL_BROWSER_BASE_URL = "https://api.firecrawl.dev"
