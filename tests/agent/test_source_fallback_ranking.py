from opensprite.agent.source_fallback_ranking import rank_web_sources_for_objective, web_source_relevance_score


def test_rank_web_sources_for_objective_prefers_relevant_title_and_snippet():
    sources = [
        {
            "title": "General AI market overview",
            "url": "https://example.com/ai",
            "snippet": "Broad enterprise AI adoption trends.",
        },
        {
            "title": "TSMC stock quote",
            "url": "https://finance.example.com/tsmc",
            "snippet": "Latest TSMC share price and Taiwan Semiconductor quote.",
        },
    ]

    ranked = rank_web_sources_for_objective(sources, "幫我找一下台積電 TSMC 股價")

    assert ranked[0]["title"] == "TSMC stock quote"


def test_web_source_relevance_score_prefers_brand_domain_from_objective():
    official = {
        "title": "OpenRouter docs",
        "url": "https://openrouter.ai/docs/api-reference",
    }
    unrelated = {
        "title": "API docs",
        "url": "https://example.com/openrouter",
    }

    objective = "Check the latest OpenRouter API docs"

    assert web_source_relevance_score(official, objective) > web_source_relevance_score(unrelated, objective)
