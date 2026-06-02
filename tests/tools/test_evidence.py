import json

from opensprite.tools.evidence import build_tool_evidence
from opensprite.tools.result_status import tool_error_result


def test_web_fetch_evidence_includes_source_quality_metadata():
    result = json.dumps(
        {
            "type": "web_fetch",
            "success": True,
            "url": "https://example.com/docs",
            "final_url": "https://example.com/docs?ref=1",
            "title": "Example Docs",
            "content": "Short extracted page text.",
            "provider": "web_fetch",
            "extractor": "trafilatura",
            "truncated": False,
            "content_chars": 26,
            "has_title": True,
            "is_too_short": True,
            "min_content_chars": 800,
        }
    )

    evidence = build_tool_evidence("web_fetch", {"url": "https://example.com/docs"}, result, ok=True)

    source = evidence.metadata["sources"][0]
    assert source["tool_name"] == "web_fetch"
    assert source["url"] == "https://example.com/docs?ref=1"
    assert source["title"] == "Example Docs"
    assert source["content_chars"] == 26
    assert source["has_title"] is True
    assert source["is_too_short"] is True
    assert source["min_content_chars"] == 800
    assert source["truncated"] is False
    assert source["extractor"] == "trafilatura"


def test_web_fetch_http_error_marks_evidence_failed():
    evidence = build_tool_evidence(
        "web_fetch",
        {"url": "https://finance.yahoo.com/quote/2330.TW/"},
        tool_error_result(
            "HTTP Error: 404 Not Found",
            error_type="ToolExecutionError",
            metadata={"tool_name": "web_fetch"},
        ),
        ok=True,
    )

    assert evidence.ok is False
    assert "sources" not in evidence.metadata
    assert "HTTP Error: 404" in evidence.metadata["error"]


def test_verify_evidence_includes_structured_verification_status():
    evidence = build_tool_evidence(
        "verify",
        {"action": "auto"},
        "Verification skipped: no supported Python or package.json build checks were detected.",
        ok=True,
    )

    assert evidence.ok is True
    assert evidence.metadata["verification_status"] == "skipped"
    assert evidence.metadata["verification_ok"] is False
    assert evidence.metadata["verification_attempted"] is True
    assert evidence.metadata["verification_name"] == "no supported Python or package.json build checks were detected."


def test_structured_web_search_error_marks_evidence_failed():
    result = json.dumps(
        {
            "type": "web_search",
            "ok": False,
            "query": "sqlite fts",
            "provider": "duckduckgo",
            "backend": "ddgs",
            "items": [],
            "error": "DuckDuckGo returned no results for 'sqlite fts'.",
        }
    )

    evidence = build_tool_evidence("web_search", {"query": "sqlite fts"}, result, ok=True)

    assert evidence.ok is False
    assert evidence.metadata["error"] == "DuckDuckGo returned no results for 'sqlite fts'."


def test_structured_web_search_error_without_text_prefix_marks_evidence_failed():
    result = json.dumps(
        {
            "type": "web_search",
            "ok": False,
            "query": "sqlite fts",
            "items": [],
            "error": "DuckDuckGo returned no results for sqlite fts.",
        }
    )

    evidence = build_tool_evidence("web_search", {"query": "sqlite fts"}, result, ok=True)

    assert evidence.ok is False
    assert evidence.metadata["error"] == "DuckDuckGo returned no results for sqlite fts."


def test_web_search_without_traceable_sources_marks_evidence_failed():
    result = json.dumps(
        {
            "type": "web_search",
            "query": "sqlite fts",
            "provider": "duckduckgo",
            "backend": "ddgs",
            "items": [],
        }
    )

    evidence = build_tool_evidence("web_search", {"query": "sqlite fts"}, result, ok=True)

    assert evidence.ok is False
    assert evidence.metadata["source_count"] == 0
    assert evidence.metadata["result_count"] == 0
    assert evidence.metadata["query"] == "sqlite fts"
    assert evidence.metadata["provider"] == "duckduckgo"
    assert evidence.metadata["backend"] == "ddgs"
    assert evidence.metadata["error"] == "web_search returned no traceable sources"


def test_web_search_with_traceable_source_remains_successful_evidence():
    result = json.dumps(
        {
            "type": "web_search",
            "query": "sqlite fts",
            "provider": "duckduckgo",
            "backend": "ddgs",
            "items": [
                {
                    "title": "SQLite FTS5",
                    "url": "https://sqlite.org/fts5.html",
                    "content": "Official full text search docs",
                }
            ],
        }
    )

    evidence = build_tool_evidence("web_search", {"query": "sqlite fts"}, result, ok=True)

    assert evidence.ok is True
    assert evidence.metadata["source_count"] == 1
    assert evidence.metadata["sources"][0]["url"] == "https://sqlite.org/fts5.html"
    assert evidence.metadata["sources"][0]["backend"] == "ddgs"


def test_exec_http_command_records_external_warning_metadata():
    evidence = build_tool_evidence(
        "exec",
        {"command": 'curl -s "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json"'},
        '{"stat":"OK"}',
        ok=True,
    )

    assert evidence.ok is True
    assert evidence.metadata["external_http_via_exec"] is True
    assert evidence.metadata["warning"] == "external HTTP fetched via exec instead of web_fetch"
    assert evidence.metadata["urls"] == ["https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json"]


def test_web_research_without_sources_marks_evidence_failed():
    result = json.dumps(
        {
            "type": "web_research",
            "query": "GPT Image workflow",
            "backend": "ddgs",
            "sources": [],
            "fetched_sources": [],
            "failed_sources": [{"reason": "web_search returned no structured result with fetchable URLs"}],
            "source_count": 0,
            "fetched_count": 0,
            "coverage": {
                "target_fetch_count": 4,
                "target_met": False,
                "search_result_count": 0,
                "fetched_count": 0,
                "failed_count": 1,
            },
        }
    )

    evidence = build_tool_evidence("web_research", {"query": "GPT Image workflow"}, result, ok=True)

    assert evidence.ok is False
    assert evidence.metadata["source_count"] == 0
    assert evidence.metadata["fetched_count"] == 0
    assert evidence.metadata["coverage"]["target_met"] is False
    assert evidence.metadata["backend"] == "ddgs"
    assert evidence.metadata["error"] == "web_research returned no traceable sources"


def test_web_research_with_sources_remains_successful_evidence():
    result = json.dumps(
        {
            "type": "web_research",
            "query": "AI browser pricing",
            "backend": "ddgs",
            "sources": [
                {
                    "url": "https://docs.test/browser",
                    "title": "AI Browser Docs",
                    "content": "Official AI browser documentation with enough detail.",
                    "content_chars": 1200,
                    "has_main_content": True,
                    "search_backend": "ddgs",
                }
            ],
            "source_count": 1,
            "fetched_count": 1,
            "coverage": {"target_fetch_count": 1, "target_met": True, "fetched_count": 1},
        }
    )

    evidence = build_tool_evidence("web_research", {"query": "AI browser pricing"}, result, ok=True)

    assert evidence.ok is True
    assert evidence.metadata["source_count"] == 1
    assert evidence.metadata["sources"][0]["url"] == "https://docs.test/browser"
    assert evidence.metadata["sources"][0]["search_backend"] == "ddgs"
