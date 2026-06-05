from opensprite.agent.completion_blocker_policy import CompletionBlockerMessages
from opensprite.agent.completion_gate import CompletionGateResult
from opensprite.agent.completion_gate_policy import ASSISTANT_RESPONSE_DID_NOT_COMPLETE_REASON
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.response_shape_policy import TERSE_FINAL_ANSWER_REASON
from opensprite.agent.web_source_policy import SOURCE_MATERIAL_INSUFFICIENT_REASON
from opensprite.agent.task_artifact import TaskArtifact
from opensprite.agent.task_contract import EvidenceRequirement, TaskContract
from opensprite.agent.turn_runner import (
    SourceFallbackMessages,
    _final_response_after_exhausted_continuation as _raw_final_response_after_exhausted_continuation,
    _message_with_runtime_context,
)


SOURCE_FALLBACK_MESSAGES = SourceFallbackMessages(
    intro="TEST SOURCE FALLBACK INTRO",
    answer_header="TEST ANSWER",
    details_header="TEST DETAILS",
    sources_header="TEST SOURCES",
)
COMPLETION_BLOCKER_MESSAGES = CompletionBlockerMessages(
    intro="TEST COMPLETION BLOCKER INTRO",
    reason_prefix="TEST REASON: ",
    detail_header="TEST DETAIL",
    missing_evidence_header="TEST MISSING",
    stop_notice="TEST STOP NOTICE",
)


def _final_response_after_exhausted_continuation(**kwargs):
    kwargs.setdefault("source_fallback_messages", SOURCE_FALLBACK_MESSAGES)
    kwargs.setdefault("completion_blocker_messages", COMPLETION_BLOCKER_MESSAGES)
    return _raw_final_response_after_exhausted_continuation(**kwargs)


def test_message_with_runtime_context_adds_cli_gateway_and_snapshot_details():
    message = _message_with_runtime_context(
        "幫我確認目前服務 healthz 是否正常",
        {
            "source": "cli_via_web",
            "gateway_url": "http://127.0.0.1:8765",
            "workspace_snapshot": {
                "path": "repo",
                "source": "C:\\Users\\win10\\Desktop\\HsinPuRepository\\opensprite",
            },
        },
    )

    assert "幫我確認目前服務 healthz 是否正常" in message
    assert "http://127.0.0.1:8765" in message
    assert "http://127.0.0.1:8765/healthz" in message
    assert "`repo/`" in message
    assert "omit VCS internals" in message


def test_exhausted_continuation_replaces_progress_only_response():
    response = _final_response_after_exhausted_continuation(
        response="有搜尋結果了，讓我進一步抓取實質內容來源的股價數據。",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason=SOURCE_MATERIAL_INSUFFICIENT_REASON,
            active_task_detail=(
                "- Web research coverage gap: fetched source coverage did not satisfy the research pass.\n"
                "- Target fetch count not met: need 2, fetched 1."
            ),
        ),
        auto_continue_attempts=3,
    )

    assert "TEST COMPLETION BLOCKER INTRO" in response
    assert f"TEST REASON: {SOURCE_MATERIAL_INSUFFICIENT_REASON}" in response
    assert "TEST DETAIL" in response
    assert SOURCE_MATERIAL_INSUFFICIENT_REASON in response
    assert "Target fetch count not met: need 2, fetched 1." in response
    assert "讓我進一步" not in response


def test_exhausted_continuation_uses_structured_blocker_status():
    original = "我無法完成查詢，因為來源不足。"

    response = _final_response_after_exhausted_continuation(
        response=original,
        completion_result=CompletionGateResult(
            status="incomplete",
            reason=SOURCE_MATERIAL_INSUFFICIENT_REASON,
        ),
        auto_continue_attempts=2,
    )

    assert response != original
    assert "TEST COMPLETION BLOCKER INTRO" in response
    assert SOURCE_MATERIAL_INSUFFICIENT_REASON in response


def test_exhausted_continuation_uses_gathered_web_sources_for_progress_only_response():
    response = _final_response_after_exhausted_continuation(
        response="找到了正確網址，讓我抓取主要文件頁面的內容。",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason=TERSE_FINAL_ANSWER_REASON,
            active_task_detail="Provide a substantive final answer that uses the gathered web source results.",
        ),
        auto_continue_attempts=3,
        execution_result=ExecutionResult(
            content="找到了正確網址，讓我抓取主要文件頁面的內容。",
            task_contract=TaskContract(
                objective="Find the current OpenRouter API parameter details and cite sources.",
                task_type="web_research",
            ),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_fetch",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://openrouter.ai/docs/api/reference/parameters",
                                "title": "OpenRouter API Parameters",
                                "snippet": "Max Tokens sets the upper limit for generated output tokens.",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert "TEST DETAILS" in response
    assert "TEST SOURCES" in response
    assert "https://openrouter.ai/docs/api/reference/parameters" in response
    assert "TEST COMPLETION BLOCKER INTRO" not in response


def test_blocked_web_research_empty_response_uses_gathered_source_fallback():
    response = _final_response_after_exhausted_continuation(
        response="Sorry, I did not produce a displayable reply.",
        completion_result=CompletionGateResult(
            status="blocked",
            reason="Web research succeeded but no final answer was delivered.",
            missing_evidence=("Substantive final answer with source references",),
        ),
        auto_continue_attempts=0,
        execution_result=ExecutionResult(
            content="Sorry, I did not produce a displayable reply.",
            task_contract=TaskContract(
                objective="Find 2026 AI agent tools market trends and cite sources.",
                task_type="web_research",
            ),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_research",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://example.com/agent-trends",
                                "title": "AI Agent Trends",
                                "snippet": "Agent tools are moving from pilots into governed production workflows.",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert "TEST DETAILS" in response
    assert "TEST SOURCES" in response
    assert "https://example.com/agent-trends" in response
    assert "TEST COMPLETION BLOCKER INTRO" not in response


def test_source_fallback_uses_configured_intro_without_text_marker_policy():
    response = _final_response_after_exhausted_continuation(
        response="Sorry, I did not produce a displayable reply.",
        completion_result=CompletionGateResult(
            status="blocked",
            reason="Web research succeeded but no final answer was delivered.",
            missing_evidence=("Substantive final answer with source references",),
        ),
        auto_continue_attempts=0,
        source_fallback_messages=SourceFallbackMessages(
            intro="TEST CONFIG INTRO",
            answer_header="TEST ANSWER",
            details_header="TEST DETAILS",
            sources_header="TEST SOURCES",
        ),
        execution_result=ExecutionResult(
            content="Sorry, I did not produce a displayable reply.",
            task_contract=TaskContract(
                objective="Find 2026 AI agent tools market trends and cite sources.",
                task_type="web_research",
            ),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_research",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://example.com/agent-trends",
                                "title": "AI Agent Trends",
                                "snippet": "Agent tools are moving from pilots into governed production workflows.",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert "TEST CONFIG INTRO" in response
    assert "https://example.com/agent-trends" in response


def test_exhausted_continuation_uses_search_snippet_when_it_contains_missing_evidence_url():
    response = _final_response_after_exhausted_continuation(
        response="抱歉，我剛剛沒有產生可顯示的回覆，請再試一次。",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason="Final answer with OpenRouter API base URL https://openrouter.ai/api/v1 was not delivered.",
            missing_evidence=("OpenRouter API base URL https://openrouter.ai/api/v1",),
        ),
        auto_continue_attempts=3,
        execution_result=ExecutionResult(
            content="抱歉，我剛剛沒有產生可顯示的回覆，請再試一次。",
            task_contract=TaskContract(
                objective="Find the OpenRouter API base URL and cite sources.",
                task_type="web_research",
            ),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_research",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_search",
                                "url": "https://dlthub.com/context/source/openrouter",
                                "title": "OpenRouter Python API Docs",
                                "snippet": "The REST API base URL is https://openrouter.ai/api/v1 and requests require a Bearer token.",
                                "content_chars": 150,
                                "has_main_content": False,
                                "is_too_short": False,
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert "TEST ANSWER" in response
    assert "https://openrouter.ai/api/v1" in response
    assert "https://openrouter.ai/api/v1 (source: https://dlthub.com/context/source/openrouter)" in response
    assert "https://dlthub.com/context/source/openrouter" in response
    assert "TEST SOURCE FALLBACK INTRO" not in response
    assert "TEST COMPLETION BLOCKER INTRO" not in response


def test_exhausted_continuation_extracts_base_url_from_source_when_missing_evidence_is_generic():
    response = _final_response_after_exhausted_continuation(
        response="抱歉，我剛剛沒有產生可顯示的回覆，請再試一次。",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason="Final answer with OpenRouter API base URL was not delivered.",
            missing_evidence=("OpenRouter API base URL",),
        ),
        auto_continue_attempts=3,
        execution_result=ExecutionResult(
            content="抱歉，我剛剛沒有產生可顯示的回覆，請再試一次。",
            task_contract=TaskContract(
                objective="Find the OpenRouter API base URL and cite sources.",
                task_type="web_research",
            ),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_research",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_search",
                                "url": "https://dlthub.com/context/source/openrouter",
                                "title": "OpenRouter Python API Docs",
                                "snippet": (
                                    "The REST API base URL is https://openrouter.ai/api/v1. "
                                    "Other examples include https://openrouter.apify.actor/api/v1 and http://localhost:11434/v1."
                                ),
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert "TEST ANSWER" in response
    assert "https://openrouter.ai/api/v1" in response
    assert "https://openrouter.ai/api/v1 (source: https://dlthub.com/context/source/openrouter)" in response
    assert "https://openrouter.apify.actor/api/v1" not in response
    assert "http://localhost:11434/v1" not in response
    assert "TEST SOURCE FALLBACK INTRO" not in response


def test_exhausted_continuation_strips_markdown_links_from_source_fallback_snippets():
    response = _final_response_after_exhausted_continuation(
        response="Let me keep checking that.",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason=TERSE_FINAL_ANSWER_REASON,
            active_task_detail="Provide a substantive final answer that uses the gathered web source results.",
        ),
        auto_continue_attempts=3,
        execution_result=ExecutionResult(
            content="Let me keep checking that.",
            task_contract=TaskContract(
                objective="Find the current TSM quote and cite sources.",
                task_type="web_research",
            ),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_fetch",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://finance.yahoo.com/quote/TSM/",
                                "title": "TSM Stock Price",
                                "snippet": (
                                    "[![](/img/ad.gif)](https://example.com/registration/) "
                                    "Latest quote details are available on the quote page."
                                ),
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert "Latest quote details" in response
    assert "https://example.com/registration/" not in response
    assert "![]" not in response


def test_exhausted_continuation_uses_gathered_source_fallback_for_market_quote():
    response = _final_response_after_exhausted_continuation(
        response="Let me keep checking that.",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason=TERSE_FINAL_ANSWER_REASON,
            active_task_detail="State the current or latest available quote directly before listing sources.",
        ),
        auto_continue_attempts=3,
        execution_result=ExecutionResult(
            content="Let me keep checking that.",
            task_contract=TaskContract(
                objective="幫我找一下台積電 ADR 目前最新股價或最接近可查到的報價，並附來源。",
                task_type="web_research",
            ),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_fetch",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://finance.yahoo.com/quote/TSM/",
                                "title": "TSM Stock Price",
                                "snippet": "Yahoo Finance quote page for Taiwan Semiconductor Manufacturing Company.",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert "TEST DETAILS" in response
    assert "TEST SOURCES" in response
    assert "https://finance.yahoo.com/quote/TSM/" in response
    assert "TEST COMPLETION BLOCKER INTRO" not in response


def test_exhausted_continuation_uses_web_contract_sources_for_generic_incomplete_reason():
    response = _final_response_after_exhausted_continuation(
        response="Let me keep checking that.",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason=ASSISTANT_RESPONSE_DID_NOT_COMPLETE_REASON,
        ),
        auto_continue_attempts=3,
        execution_result=ExecutionResult(
            content="Let me keep checking that.",
            task_contract=TaskContract(
                objective="Find the current OpenRouter API base URL and cite sources.",
                task_type="web_research",
                requirements=(EvidenceRequirement(kind="tool_group", tool_group="web_research"),),
            ),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_research",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://openrouter.ai/docs/api-reference/overview",
                                "title": "OpenRouter API Overview",
                                "snippet": "OpenRouter requests use the https://openrouter.ai/api/v1 base URL.",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert "TEST DETAILS" in response
    assert "TEST SOURCES" in response
    assert "https://openrouter.ai/docs/api-reference/overview" in response
    assert "TEST COMPLETION BLOCKER INTRO" not in response


def test_exhausted_continuation_does_not_use_source_fallback_from_reason_only():
    response = _final_response_after_exhausted_continuation(
        response="Let me keep checking that.",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason=TERSE_FINAL_ANSWER_REASON,
        ),
        auto_continue_attempts=3,
        execution_result=ExecutionResult(
            content="Let me keep checking that.",
            task_contract=TaskContract(
                objective="Answer a plain question without web research.",
                task_type="pure_answer",
            ),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_fetch",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://example.com/source",
                                "title": "Example Source",
                                "snippet": "A source that should not be used without a web-source contract.",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert "https://example.com/source" not in response
    assert TERSE_FINAL_ANSWER_REASON in response


def test_exhausted_continuation_uses_gathered_web_sources_after_optional_tool_error():
    response = _final_response_after_exhausted_continuation(
        response="Cannot reliably complete this request because one optional fetch failed.",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason="tool execution reported an error without a clear blocker handoff",
        ),
        auto_continue_attempts=3,
        execution_result=ExecutionResult(
            content="Cannot reliably complete this request.",
            had_tool_error=True,
            task_contract=TaskContract(objective="幫我查 OpenRouter API base URL，請列出來源網址。", task_type="web_research"),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_research",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://openrouter.ai/docs/api/reference/overview",
                                "title": "OpenRouter API Reference",
                                "snippet": "OpenRouter API requests use the https://openrouter.ai/api/v1 base URL.",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert "https://openrouter.ai/docs/api/reference/overview" in response
    assert "tool execution reported an error without a clear blocker handoff" not in response


def test_optional_tool_error_source_fallback_ranks_relevant_sources_first():
    response = _final_response_after_exhausted_continuation(
        response="Cannot reliably complete this request because one optional fetch failed.",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason="tool execution reported an error without a clear blocker handoff",
        ),
        auto_continue_attempts=3,
        execution_result=ExecutionResult(
            content="Cannot reliably complete this request.",
            had_tool_error=True,
            task_contract=TaskContract(objective="幫我查 OpenRouter API base URL，請列出來源網址。", task_type="web_research"),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_research",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://example.com/general-ai-commentary",
                                "title": "General AI Commentary",
                                "snippet": "A broad AI article without the requested API base URL.",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            },
                            {
                                "tool_name": "web_fetch",
                                "url": "https://openrouter.ai/docs/api/reference/overview",
                                "title": "OpenRouter API Reference",
                                "snippet": "OpenRouter API requests use the https://openrouter.ai/api/v1 base URL.",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            },
                        ]
                    },
                ),
            ),
        ),
    )

    assert response.index("https://openrouter.ai/docs/api/reference/overview") < response.index("https://example.com/general-ai-commentary")


def test_optional_tool_error_source_fallback_prefers_official_brand_domain():
    response = _final_response_after_exhausted_continuation(
        response="Cannot reliably complete this request because one optional fetch failed.",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason="tool execution reported an error without a clear blocker handoff",
        ),
        auto_continue_attempts=3,
        execution_result=ExecutionResult(
            content="Cannot reliably complete this request.",
            had_tool_error=True,
            task_contract=TaskContract(objective="請查一下 OpenRouter 目前文件裡 Authorization header 怎麼寫，附來源網址。", task_type="web_research"),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_research",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://dlthub.com/docs/dlt-ecosystem/verified-sources/openrouter",
                                "domain": "dlthub.com",
                                "title": "OpenRouter Python API Docs | dltHub",
                                "snippet": "Third-party OpenRouter pipeline docs.",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            },
                            {
                                "tool_name": "web_fetch",
                                "url": "https://openrouter.ai/docs",
                                "domain": "openrouter.ai",
                                "title": "OpenRouter Docs",
                                "snippet": "Authorization: Bearer <OPENROUTER_API_KEY>",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            },
                        ]
                    },
                ),
            ),
        ),
    )

    assert response.index("https://openrouter.ai/docs") < response.index("https://dlthub.com/docs/dlt-ecosystem/verified-sources/openrouter")


def test_incomplete_fallback_response_is_replaced_without_continuation_attempts():
    response = _final_response_after_exhausted_continuation(
        response="抱歉，我剛剛沒有產生可顯示的回覆，請再試一次。",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason="tool execution reported an error without a clear blocker handoff",
        ),
        auto_continue_attempts=0,
    )

    assert "tool execution reported an error without a clear blocker handoff" in response
    assert "沒有產生可顯示" not in response


def test_complete_response_is_not_replaced_after_continuation():
    original = "已完成，這是整理結果。"

    response = _final_response_after_exhausted_continuation(
        response=original,
        completion_result=CompletionGateResult(status="complete", reason="answered"),
        auto_continue_attempts=1,
    )

    assert response == original
