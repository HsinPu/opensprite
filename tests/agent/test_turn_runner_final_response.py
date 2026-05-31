from opensprite.agent.completion_gate import CompletionGateResult
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.task_artifact import TaskArtifact
from opensprite.agent.task_contract import TaskContract
from opensprite.agent.turn_runner import _final_response_after_exhausted_continuation


def test_exhausted_continuation_replaces_progress_only_response():
    response = _final_response_after_exhausted_continuation(
        response="有搜尋結果了，讓我進一步抓取實質內容來源的股價數據。",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason="required source material was insufficient",
            active_task_detail=(
                "- Web research coverage gap: fetched source coverage did not satisfy the research pass.\n"
                "- Target fetch count not met: need 2, fetched 1."
            ),
        ),
        auto_continue_attempts=3,
    )

    assert "目前還不能可靠完成這次請求" in response
    assert "required source material was insufficient" in response
    assert "Target fetch count not met: need 2, fetched 1." in response
    assert "讓我進一步" not in response


def test_exhausted_continuation_keeps_clear_blocker_response():
    original = "我無法完成查詢，因為來源不足。"

    response = _final_response_after_exhausted_continuation(
        response=original,
        completion_result=CompletionGateResult(
            status="incomplete",
            reason="required source material was insufficient",
        ),
        auto_continue_attempts=2,
    )

    assert response == original


def test_exhausted_continuation_uses_gathered_web_sources_for_progress_only_response():
    response = _final_response_after_exhausted_continuation(
        response="找到了正確網址，讓我抓取主要文件頁面的內容。",
        completion_result=CompletionGateResult(
            status="incomplete",
            reason="assistant final answer was too terse for the task",
            active_task_detail="Provide a substantive final answer that uses the gathered web source results.",
        ),
        auto_continue_attempts=3,
        execution_result=ExecutionResult(
            content="找到了正確網址，讓我抓取主要文件頁面的內容。",
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

    assert "重點摘要" in response
    assert "https://openrouter.ai/docs/api/reference/parameters" in response
    assert "目前還不能可靠完成這次請求" not in response


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
            task_contract=TaskContract(objective="幫我查一下台積電目前股價，請列出來源網址。", task_type="web_research"),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_research",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://tw.stock.yahoo.com/quote/2330.TW",
                                "title": "台積電(2330.TW) 股價 - Yahoo股市",
                                "snippet": "台積電 2330 股價頁面。",
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

    assert "https://tw.stock.yahoo.com/quote/2330.TW" in response
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
            task_contract=TaskContract(objective="幫我查一下台積電目前股價，請列出來源網址。", task_type="web_research"),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_research",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://example.com/general-market-commentary",
                                "title": "General Market Commentary",
                                "snippet": "A broad market article without the requested stock quote.",
                                "content_chars": 1200,
                                "has_main_content": True,
                                "is_too_short": False,
                            },
                            {
                                "tool_name": "web_fetch",
                                "url": "https://tw.stock.yahoo.com/quote/2330.TW",
                                "title": "台積電(2330.TW) 股價 - Yahoo股市",
                                "snippet": "台積電 2330 股價 2,355 收盤。",
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

    assert response.index("https://tw.stock.yahoo.com/quote/2330.TW") < response.index("https://example.com/general-market-commentary")


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
