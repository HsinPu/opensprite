from opensprite.agent.web_source_policy import (
    SOURCE_MATERIAL_INSUFFICIENT_REASON,
    normalize_source_url,
    ungrounded_response_source_urls,
    web_source_has_substantive_detail,
    web_source_is_referenced,
)


def test_source_material_insufficient_reason_is_stable():
    assert SOURCE_MATERIAL_INSUFFICIENT_REASON == "required source material was insufficient"


def test_web_source_has_substantive_detail_accepts_good_fetch_source():
    assert web_source_has_substantive_detail(
        {
            "tool_name": "web_fetch",
            "has_main_content": True,
            "is_too_short": False,
            "blocked_or_challenge": False,
            "content_chars": 1200,
            "min_content_chars": 800,
        }
    )


def test_web_source_has_substantive_detail_rejects_blocked_or_short_fetch_source():
    assert not web_source_has_substantive_detail({"tool_name": "web_fetch", "blocked_or_challenge": True})
    assert not web_source_has_substantive_detail({"tool_name": "web_fetch", "is_too_short": True})
    assert not web_source_has_substantive_detail(
        {"tool_name": "web_fetch", "has_main_content": True, "content_chars": 120, "min_content_chars": 800}
    )


def test_web_source_has_substantive_detail_requires_fetched_source_tool():
    assert not web_source_has_substantive_detail({"tool_name": "web_search", "content_chars": 1200})


def test_web_source_is_referenced_by_url_domain_or_title():
    source = {
        "url": "https://www.example.com/docs/page",
        "title": "Example Product Manual",
    }

    assert web_source_is_referenced(source, "See https://www.example.com/docs/page for details.")
    assert web_source_is_referenced(source, "The example.com documentation says this.")
    assert web_source_is_referenced(source, "The Example Product Manual covers this.")


def test_ungrounded_response_source_urls_reports_urls_not_in_sources():
    sources = [{"url": "https://example.com/docs/page"}]

    assert ungrounded_response_source_urls(
        "Use https://example.com/docs/page and https://other.example/quote.",
        sources,
    ) == ["https://other.example/quote"]


def test_ungrounded_response_source_urls_ignores_openrouter_api_base_url_reference():
    assert ungrounded_response_source_urls(
        "The API base URL is https://openrouter.ai/api/v1.",
        [{"url": "https://openrouter.ai/docs/api/reference/overview"}],
    ) == []


def test_normalize_source_url_normalizes_openrouter_doc_aliases():
    assert normalize_source_url("https://www.openrouter.ai/docs/api-reference/overview.md") == (
        "https://openrouter.ai/docs/api/reference/overview"
    )
