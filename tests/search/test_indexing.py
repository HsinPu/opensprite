import json

from opensprite.search.indexing import build_knowledge_documents, guess_tool_name


def test_guess_tool_name_detects_web_research_before_generic_payloads():
    payload = json.dumps(
        {
            "type": "web_research",
            "query": "sqlite",
            "url": "",
            "content": "combined fetched content",
            "items": [],
            "fetched_sources": [],
        }
    )

    assert guess_tool_name(payload) == "web_research"


def test_build_knowledge_documents_indexes_web_research_sources():
    result = json.dumps(
        {
            "type": "web_research",
            "query": "sqlite fts5",
            "provider": "duckduckgo",
            "content_type": "application/json",
            "items": [
                {
                    "title": "SQLite FTS5",
                    "url": "https://sqlite.org/fts5.html",
                    "content": "Official full text search docs",
                }
            ],
            "fetched_sources": [
                {
                    "title": "SQLite FTS5",
                    "url": "https://sqlite.org/fts5.html",
                    "content": "SQLite FTS5 supports full text search docs and examples.",
                    "snippet": "Official full text search docs",
                    "search_provider": "duckduckgo",
                    "extractor": "trafilatura",
                    "status": 200,
                    "content_type": "text/html",
                    "truncated": False,
                }
            ],
        }
    )

    docs = build_knowledge_documents(tool_name="web_research", tool_args={"query": "sqlite fts5"}, result=result)

    assert [doc.source_type for doc in docs] == ["web_search", "web_fetch"]
    assert [doc.tool_name for doc in docs] == ["web_research", "web_research"]
    assert docs[0].provider == "duckduckgo"
    assert docs[0].extractor == "search"
    assert docs[1].provider == "duckduckgo"
    assert docs[1].extractor == "trafilatura"
    assert docs[1].status == 200
    assert docs[1].content_type == "text/html"
