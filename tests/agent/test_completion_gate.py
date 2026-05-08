from opensprite.agent.completion_gate import CompletionGateService
from opensprite.agent.auto_continue import AutoContinueService
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.task_contract import TaskContractService
from opensprite.agent.task_intent import TaskIntentService
from opensprite.storage.base import StoredDelegatedTask
from opensprite.tools.evidence import ToolEvidence


def test_completion_gate_requires_requested_verification_before_completion():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Completed the refactor.",
        execution_result=ExecutionResult(
            content="Completed the refactor.",
            file_change_count=1,
            touched_paths=("src/agent.py",),
        ),
    )

    assert result.status == "needs_verification"
    assert result.reason == "required verification was not recorded"
    assert result.should_update_active_task is False
    assert result.verification_action == "pytest"
    assert result.verification_path == "."


def test_completion_gate_prefers_web_smoke_when_requested_for_web_changes():
    intent = TaskIntentService().classify("Please update the web UI and run test:smoke.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Updated the web UI.",
        execution_result=ExecutionResult(
            content="Updated the web UI.",
            file_change_count=1,
            touched_paths=("apps/web/src/App.vue",),
        ),
    )

    assert result.status == "needs_verification"
    assert result.verification_action == "web_smoke"
    assert result.verification_path == "apps/web"


def test_completion_gate_keeps_verification_status_when_verify_fails_with_tool_error():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Verification failed.",
        execution_result=ExecutionResult(
            content="Verification failed.",
            file_change_count=1,
            touched_paths=("src/agent.py",),
            verification_attempted=True,
            verification_passed=False,
            had_tool_error=True,
        ),
    )

    assert result.status == "needs_verification"
    assert result.reason == "required verification did not pass"
    assert result.verification_action == "pytest"
    assert result.verification_path == "."


def test_completion_gate_marks_blocked_when_tool_error_reports_blocker():
    intent = TaskIntentService().classify("繼續驗證")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="目前無法繼續，測試環境失敗。",
        execution_result=ExecutionResult(
            content="目前無法繼續，測試環境失敗。",
            executed_tool_calls=1,
            had_tool_error=True,
        ),
    )

    assert result.status == "blocked"
    assert result.active_task_status == "blocked"
    assert result.should_update_active_task is True


def test_completion_gate_marks_waiting_when_response_asks_for_input():
    intent = TaskIntentService().classify("繼續做")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="請問你要用哪個 target branch？",
        execution_result=ExecutionResult(content="請問你要用哪個 target branch？"),
    )

    assert result.status == "waiting_user"
    assert result.active_task_status == "waiting_user"
    assert result.should_update_active_task is True


def test_completion_gate_requires_web_evidence_for_external_search_task():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="正在幫你搜尋 Reddit 搜尋相關的開源專案...",
        execution_result=ExecutionResult(content="正在幫你搜尋 Reddit 搜尋相關的開源專案..."),
    )

    assert result.status == "incomplete"
    assert result.reason == "required task evidence was not produced"
    assert result.missing_evidence


def test_completion_gate_marks_progress_only_fetch_response_incomplete():
    intent = TaskIntentService().classify("看一下 ai 版 幫我抓20 筆")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="好，幫你抓 r/taiwan 熱門文章 20 筆！",
        execution_result=ExecutionResult(content="好，幫你抓 r/taiwan 熱門文章 20 筆！"),
    )

    assert result.status == "incomplete"
    assert result.reason == "assistant did not provide the requested itemized result"


def test_completion_gate_marks_direct_reply_instruction_complete_without_marker():
    intent = TaskIntentService().classify("請只回覆這三個英文詞，且不要加入其他文字：alpha beta gamma")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="alpha beta gamma",
        execution_result=ExecutionResult(content="alpha beta gamma"),
    )

    assert intent.kind == "task"
    assert result.status == "complete"
    assert result.reason == "direct reply instruction received a response"


def test_auto_continue_allows_first_retry_after_missing_web_evidence():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="正在幫你搜尋 Reddit 搜尋相關的開源專案...",
        execution_result=ExecutionResult(content="正在幫你搜尋 Reddit 搜尋相關的開源專案..."),
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="正在幫你搜尋 Reddit 搜尋相關的開源專案..."),
        attempts_used=0,
        previous_response="正在幫你搜尋 Reddit 搜尋相關的開源專案...",
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_incomplete"
    assert "Continue the current task" in (decision.prompt or "")
    assert "Required follow-up" in (decision.prompt or "")


def test_auto_continue_allows_first_retry_after_progress_only_fetch_response():
    intent = TaskIntentService().classify("看一下 ai 版 幫我抓20 筆")
    response = "好，幫你抓 r/taiwan 熱門文章 20 筆！"
    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content=response),
        attempts_used=0,
        previous_response=response,
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_incomplete"
    assert "Continue the current task" in (decision.prompt or "")


def test_completion_gate_marks_chinese_action_ack_response_incomplete():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理"
    )
    response = "好，我來分析全部 4 張圖片並整理 Prompt！"
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/a.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/b.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/c.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/d.jpg"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response, task_contract=contract),
    )

    exec_result = ExecutionResult(content=response, task_contract=contract)
    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=exec_result,
        attempts_used=0,
        previous_response=response,
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert "image:images/a.jpg" in "\n".join(completion.missing_evidence)
    assert decision.should_continue is True
    assert "Continue the current task" in (decision.prompt or "")


def test_completion_gate_marks_generic_chinese_intent_to_act_response_incomplete():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理"
    )
    response = "好，我馬上處理這 4 張圖片。"
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/a.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/b.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/c.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/d.jpg"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response, task_contract=contract),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"


def test_completion_gate_completes_media_contract_when_all_images_have_evidence():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理"
    )
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/a.jpg"},
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/b.jpg"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Prompt 1: ...\n\n整合版：...",
        execution_result=ExecutionResult(
            content="Prompt 1: ...\n\n整合版：...",
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(
                ToolEvidence(name="ocr_image", resource_ids=("image:images/a.jpg",), ok=True),
                ToolEvidence(name="analyze_image", resource_ids=("image:images/b.jpg",), ok=True),
            ),
        ),
    )

    assert completion.status == "complete"
    assert completion.missing_evidence == ()


def test_completion_gate_accepts_current_turn_media_index_evidence_for_saved_files():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理",
        images=["data:image/jpeg;base64,abc", "data:image/jpeg;base64,def"],
    )
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        current_image_files=["images/a.jpg", "images/b.jpg"],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Prompt 1: ...\nPrompt 2: ...\n\n整合版：...",
        execution_result=ExecutionResult(
            content="Prompt 1: ...\nPrompt 2: ...\n\n整合版：...",
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(
                ToolEvidence(name="ocr_image", resource_ids=("image_index:0",), ok=True),
                ToolEvidence(name="ocr_image", resource_ids=("image_index:1",), ok=True),
            ),
        ),
    )

    assert completion.status == "complete"
    assert completion.missing_evidence == ()


def test_completion_gate_does_not_mark_short_answer_as_progress_only():
    intent = TaskIntentService().classify("你建議哪個方案？")
    response = "我建議用 RSS，因為不需要申請 API key。"

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert completion.status == "complete"
    assert completion.reason == "one-turn intent received a response"


def test_completion_gate_marks_internal_only_response_incomplete():
    intent = TaskIntentService().classify("幫我抓 Reddit ai 版 20 筆")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="",
        execution_result=ExecutionResult(
            content="",
            assistant_internal_only_response=True,
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "assistant only emitted internal control text"


def test_auto_continue_guides_retry_after_internal_only_response():
    intent = TaskIntentService().classify("幫我抓 Reddit ai 版 20 筆")
    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="",
        execution_result=ExecutionResult(
            content="",
            assistant_internal_only_response=True,
        ),
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(
            content="",
            assistant_internal_only_response=True,
        ),
        attempts_used=0,
        previous_response="",
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_incomplete"
    assert "only contained internal control text" in (decision.prompt or "")
    assert "Do not repeat internal tags" in (decision.prompt or "")


def test_completion_gate_marks_explicit_task_completion_done():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="No major findings.",
                    metadata={
                        "structured_output": {
                            "status": "ok",
                            "summary": "No major findings.",
                            "finding_count": 0,
                        }
                    },
                ),
            ),
        ),
    )

    assert result.status == "complete"
    assert result.active_task_status == "done"
    assert result.should_update_active_task is True


def test_completion_gate_requires_recorded_code_changes_for_implementation():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(content="Implemented the final cleanup successfully."),
    )

    assert result.status == "incomplete"
    assert result.reason == "expected code changes were not recorded"


def test_completion_gate_requires_review_for_code_changes_without_review_evidence():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
        ),
    )

    assert result.status == "needs_review"
    assert result.reason == "delegated review was not recorded for code changes"
    assert result.review_required is True
    assert result.review_attempted is False
    assert "delegated review step" in (result.active_task_detail or "")


def test_completion_gate_requires_follow_up_when_review_reports_findings():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="One correctness risk found.",
                    metadata={
                        "structured_output": {
                            "status": "ok",
                            "summary": "One correctness risk found.",
                            "finding_count": 1,
                        }
                    },
                ),
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.reason == "delegated review reported findings that require follow-up"
    assert result.review_attempted is True
    assert result.review_passed is False
    assert result.review_finding_count == 1
    assert "correctness risk" in (result.active_task_detail or "")


def test_completion_gate_prefers_structured_review_fix_for_follow_up_detail():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Implemented the final cleanup successfully.",
        execution_result=ExecutionResult(
            content="Implemented the final cleanup successfully.",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="One high-risk bug found.",
                    metadata={
                        "structured_output": {
                            "status": "ok",
                            "summary": "One high-risk bug found.",
                            "finding_count": 1,
                            "sections": [
                                {
                                    "key": "findings",
                                    "title": "Review Findings",
                                    "type": "finding_list",
                                    "items": [
                                        {
                                            "title": "Null handling bug",
                                            "path": "src/foo.py",
                                            "why": "Empty input can raise an exception.",
                                            "fix": "Guard the null path before dereference.",
                                        }
                                    ],
                                }
                            ],
                        }
                    },
                ),
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.active_task_detail == "src/foo.py: Null handling bug: Guard the null path before dereference."


def test_completion_gate_allows_workflow_completion_with_clean_review():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow: implement_then_review\nStatus: completed",
        execution_result=ExecutionResult(
            content="Workflow: implement_then_review\nStatus: completed",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            delegated_tasks=(
                StoredDelegatedTask(
                    task_id="task_review",
                    prompt_type="code-reviewer",
                    status="completed",
                    summary="No major findings.",
                    metadata={"structured_output": {"status": "ok", "summary": "No major findings.", "finding_count": 0}},
                ),
            ),
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "review_attempted": True,
                    "review_passed": True,
                    "review_finding_count": 0,
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "complete"
    assert result.reason == "workflow implement_then_review completed with clean review evidence"


def test_completion_gate_uses_workflow_review_finding_detail_without_delegated_tasks():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow: implement_then_review\nStatus: completed",
        execution_result=ExecutionResult(
            content="Workflow: implement_then_review\nStatus: completed",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "review_attempted": True,
                    "review_passed": False,
                    "review_finding_count": 1,
                    "review_summary": "One high-risk bug found.",
                    "review_first_finding": "src/foo.py: Null handling bug: Guard the null path before dereference.",
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.reason == "workflow implement_then_review completed but review findings still require follow-up"
    assert result.active_task_detail == "src/foo.py: Null handling bug: Guard the null path before dereference."
    assert result.follow_up_workflow == "implement_then_review"
    assert result.follow_up_step_id == "implement"
    assert result.follow_up_step_label == "Implement"
    assert result.follow_up_prompt_type == "implementer"
    assert result.review_attempted is True
    assert result.review_finding_count == 1


def test_completion_gate_prioritizes_workflow_review_follow_up_before_verification():
    intent = TaskIntentService().classify("Please refactor the agent and run tests.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow result attached.",
        execution_result=ExecutionResult(
            content="Workflow result attached.",
            file_change_count=1,
            touched_paths=("src/agent.py",),
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "review_attempted": True,
                    "review_passed": False,
                    "review_finding_count": 1,
                    "review_summary": "One high-risk bug found.",
                    "review_first_finding": "src/foo.py: Null handling bug: Guard the null path before dereference.",
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.follow_up_step_id == "implement"


def test_completion_gate_sets_workflow_review_step_target_when_review_is_missing():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow: implement_then_review\nStatus: completed",
        execution_result=ExecutionResult(
            content="Workflow: implement_then_review\nStatus: completed",
            file_change_count=1,
            touched_paths=("src/cleanup.py",),
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "completed",
                    "review_attempted": False,
                    "review_passed": False,
                    "review_finding_count": 0,
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "needs_review"
    assert result.follow_up_workflow == "implement_then_review"
    assert result.follow_up_step_id == "review"
    assert result.follow_up_step_label == "Code review"
    assert result.follow_up_prompt_type == "code-reviewer"


def test_completion_gate_marks_blocked_when_workflow_fails():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow failed.",
        execution_result=ExecutionResult(
            content="Workflow failed.",
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "failed",
                    "next_step_id": "review",
                    "next_step_label": "Code review",
                    "next_step_prompt_type": "code-reviewer",
                    "error": "review step failed",
                },
            ),
        ),
    )

    assert result.status == "blocked"
    assert result.reason == "workflow implement_then_review did not complete successfully"
    assert result.active_task_detail == "Resolve the Code review step failure in implement_then_review: review step failed"
    assert result.follow_up_prompt_type == "code-reviewer"


def test_completion_gate_marks_incomplete_when_workflow_is_cancelled():
    intent = TaskIntentService().classify("Please implement the final cleanup.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow cancelled.",
        execution_result=ExecutionResult(
            content="Workflow cancelled.",
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_abc123",
                    "workflow": "implement_then_review",
                    "status": "cancelled",
                    "next_step_id": "review",
                    "next_step_label": "Code review",
                    "next_step_prompt_type": "code-reviewer",
                    "error": "cancelled",
                    "summary": "Workflow stopped after 1/2 completed step(s).",
                },
            ),
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "workflow implement_then_review did not complete successfully"
    assert result.active_task_detail == (
        "Resume with the Code review step in implement_then_review. "
        "Workflow stopped after 1/2 completed step(s)."
    )
    assert result.follow_up_prompt_type == "code-reviewer"


def test_completion_gate_allows_research_then_outline_without_completion_phrase():
    intent = TaskIntentService().classify("Help me research and outline this topic.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Workflow result attached below.",
        execution_result=ExecutionResult(
            content="Workflow result attached below.",
            workflow_outcomes=(
                {
                    "workflow_run_id": "workflow_outline",
                    "workflow": "research_then_outline",
                    "status": "completed",
                    "review_attempted": False,
                    "review_passed": False,
                    "review_finding_count": 0,
                    "verification_attempted": False,
                    "verification_passed": False,
                },
            ),
        ),
    )

    assert result.status == "complete"
    assert result.reason == "workflow research_then_outline completed all required steps"


def test_completion_gate_allows_review_without_code_changes():
    intent = TaskIntentService().classify("Please review the recent changes for regressions.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Found two regressions tied to src/app.py and tests/test_app.py.",
        execution_result=ExecutionResult(content="Found two regressions tied to src/app.py and tests/test_app.py."),
    )

    assert result.status == "complete"
    assert result.reason == "analysis-style task returned a substantive response"


def test_completion_gate_allows_debug_diagnosis_without_code_changes():
    intent = TaskIntentService().classify("Please investigate why the build is failing.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="The build fails because the generated config file is missing at startup.",
        execution_result=ExecutionResult(content="The build fails because the generated config file is missing at startup."),
    )

    assert result.status == "complete"
    assert result.reason == "debug diagnosis was provided without requiring code changes"
