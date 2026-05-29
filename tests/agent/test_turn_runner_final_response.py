from opensprite.agent.completion_gate import CompletionGateResult
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


def test_complete_response_is_not_replaced_after_continuation():
    original = "已完成，這是整理結果。"

    response = _final_response_after_exhausted_continuation(
        response=original,
        completion_result=CompletionGateResult(status="complete", reason="answered"),
        auto_continue_attempts=1,
    )

    assert response == original
