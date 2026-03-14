"""WebFetch tool wrapper for mini-bot."""

from typing import Any
from minibot.tools.base import Tool
from minibot.tools.webfetch import WebFetcher as _WebFetcher


class WebFetchTool(Tool):
    """Web fetch tool - wraps WebFetcher."""

    def __init__(self, max_chars: int = 50000, timeout: int = 30,
                 prefer_trafilatura: bool = True, firecrawl_api_key: str = None):
        self.fetcher = _WebFetcher(
            max_chars=max_chars,
            timeout=timeout,
            prefer_trafilatura=prefer_trafilatura,
            firecrawl_api_key=firecrawl_api_key
        )

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return "Fetch and extract readable content from URLs. Returns title, text, and metadata."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"},
                "max_chars": {"type": "integer", "description": "Max characters to return", "default": 50000}
            },
            "required": ["url"]
        }

    async def execute(self, url: str, max_chars: int = 50000, **kwargs: Any) -> str:
        result = self.fetcher.fetch(url)
        
        import json
        return json.dumps({
            "url": result.get("url"),
            "finalUrl": result.get("finalUrl"),
            "status": result.get("status"),
            "title": result.get("title"),
            "extractor": result.get("extractor"),
            "truncated": result.get("truncated"),
            "text": result.get("text")
        }, ensure_ascii=False)
