import json

from opensprite.tools.evidence import build_tool_evidence


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
        "Error executing web_fetch: HTTP Error: 404 Not Found",
        ok=True,
    )

    assert evidence.ok is False
    assert "sources" not in evidence.metadata
    assert "HTTP Error: 404" in evidence.metadata["error"]


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
