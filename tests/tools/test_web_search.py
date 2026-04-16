import json

from opensprite.tools.web_search import _format_results


def test_format_results_returns_structured_json_payload():
    payload = _format_results(
        "sqlite fts5",
        [
            {
                "title": "<b>SQLite FTS5</b>",
                "url": "https://sqlite.org/fts5.html",
                "content": "Official   <em>full text</em> search docs",
            }
        ],
        5,
        provider="duckduckgo",
    )

    parsed = json.loads(payload)

    assert parsed == {
        "query": "sqlite fts5",
        "provider": "duckduckgo",
        "results": [
            {
                "title": "SQLite FTS5",
                "url": "https://sqlite.org/fts5.html",
                "content": "Official full text search docs",
            }
        ],
    }
