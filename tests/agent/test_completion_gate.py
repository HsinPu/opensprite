from opensprite.agent.completion_gate import CompletionGateService
from opensprite.agent.auto_continue import AutoContinueService
from opensprite.agent.evidence_gate import EvidenceGateService
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.quality_gate import QualityGateService
from opensprite.agent.task_artifact import TaskArtifact
from opensprite.agent.task_contract import (
    AcceptanceCriterion,
    EvidenceRequirement,
    TaskContract,
    TaskContractService,
)
from opensprite.agent.task_intent import TaskIntentService
from opensprite.storage.base import StoredDelegatedTask
from opensprite.tools.evidence import ToolEvidence


def _web_source_artifact() -> TaskArtifact:
    return TaskArtifact(
        kind="web_source",
        source_tool="web_search",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_search",
                    "url": "https://www.reddit.com/dev/api/",
                    "title": "Reddit API docs",
                    "snippet": "Official Reddit API documentation for search and listings.",
                    "query": "reddit search api",
                    "provider": "duckduckgo",
                },
                {
                    "tool_name": "web_search",
                    "url": "https://www.reddit.com/wiki/api/",
                    "title": "Reddit API wiki",
                    "snippet": "Reddit API wiki with additional integration notes.",
                    "query": "reddit search api",
                    "provider": "duckduckgo",
                },
            ],
            "source_count": 2,
        },
    )


def _web_fetch_artifact(*, is_too_short: bool = False, blocked_or_challenge: bool = False) -> TaskArtifact:
    return TaskArtifact(
        kind="web_source",
        source_tool="web_fetch",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://www.reddit.com/dev/api/",
                    "title": "Reddit API docs",
                    "snippet": "Official Reddit API documentation for search, listings, authentication, and rate limits.",
                    "query": "https://www.reddit.com/dev/api/",
                    "provider": "web_fetch",
                    "content_chars": 120 if is_too_short else 1200,
                    "is_too_short": is_too_short,
                    "has_main_content": not is_too_short and not blocked_or_challenge,
                    "blocked_or_challenge": blocked_or_challenge,
                    "min_content_chars": 800,
                    "extractor": "trafilatura",
                    "truncated": False,
                }
            ],
            "source_count": 1,
        },
    )


def _web_research_artifacts() -> tuple[TaskArtifact, ...]:
    return (_web_source_artifact(), _web_fetch_artifact())


def _web_research_coverage_gap_artifact() -> TaskArtifact:
    return TaskArtifact(
        kind="web_source",
        source_tool="web_research",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://docs.test/browser",
                    "title": "AI Browser Docs",
                    "snippet": "Official AI browser documentation.",
                    "query": "ai browser",
                    "provider": "duckduckgo",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                    "min_content_chars": 800,
                    "extractor": "trafilatura",
                    "truncated": False,
                },
                {
                    "tool_name": "web_search",
                    "url": "https://pricing.test/browser",
                    "title": "AI Browser Pricing",
                    "snippet": "Pricing search result.",
                    "query": "ai browser pricing",
                    "provider": "duckduckgo",
                },
            ],
            "source_count": 2,
            "coverage": {
                "target_fetch_count": 2,
                "target_met": False,
                "search_result_count": 2,
                "fetched_count": 1,
                "failed_count": 1,
                "too_short_count": 1,
                "blocked_count": 0,
                "missing_url_count": 0,
                "fetched_domains": ["docs.test"],
                "fetched_domain_count": 1,
                "fetched_queries": ["ai browser"],
                "fetched_query_count": 1,
                "queries_with_search_results": ["ai browser", "ai browser pricing"],
                "queries_without_successful_fetch": ["ai browser pricing"],
            },
        },
    )


def _web_research_partial_query_artifact() -> TaskArtifact:
    return TaskArtifact(
        kind="web_source",
        source_tool="web_research",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://docs.test/browser",
                    "title": "AI Browser Docs",
                    "snippet": "Official AI browser documentation.",
                    "query": "ai browser",
                    "provider": "duckduckgo",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                    "min_content_chars": 800,
                },
                {
                    "tool_name": "web_fetch",
                    "url": "https://market.test/browser",
                    "title": "AI Browser Market",
                    "snippet": "Market research for AI browser tools.",
                    "query": "ai browser market",
                    "provider": "duckduckgo",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                    "min_content_chars": 800,
                },
            ],
            "source_count": 2,
            "coverage": {
                "target_fetch_count": 2,
                "target_met": True,
                "search_result_count": 4,
                "fetched_count": 2,
                "failed_count": 1,
                "too_short_count": 0,
                "blocked_count": 0,
                "missing_url_count": 0,
                "fetched_domains": ["docs.test", "market.test"],
                "fetched_domain_count": 2,
                "fetched_queries": ["ai browser", "ai browser market"],
                "fetched_query_count": 2,
                "queries_with_search_results": ["ai browser", "ai browser market", "ai browser pricing"],
                "queries_without_successful_fetch": ["ai browser pricing"],
            },
        },
    )


def _tool_group_contract(intent, task_type: str, tool_group: str) -> TaskContract:
    return TaskContract(
        objective=intent.objective,
        task_type=task_type,
        requirements=(EvidenceRequirement(kind="tool_group", tool_group=tool_group),),
        allow_no_tool_final=False,
        contract_sources=("test",),
    )


def _web_contract(intent) -> TaskContract:
    return _tool_group_contract(intent, "web_research", "web_research")


def _workspace_contract(intent) -> TaskContract:
    return _tool_group_contract(intent, "workspace_read", "workspace_read")


def _history_contract(intent) -> TaskContract:
    return _tool_group_contract(intent, "history_retrieval", "history_retrieval")


def _itemized_contract(intent) -> TaskContract:
    return TaskContract(
        objective=intent.objective,
        task_type="pure_answer",
        acceptance_criteria=(AcceptanceCriterion(kind="itemized_output", min_count=3, max_response_chars=260),),
        allow_no_tool_final=True,
        contract_sources=("test",),
    )


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


def test_completion_gate_treats_max_tool_iterations_as_incomplete():
    intent = TaskIntentService().classify("Please implement the cleanup and run tests.")

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="I hit the iteration limit before finishing.",
        execution_result=ExecutionResult(
            content="I hit the iteration limit before finishing.",
            executed_tool_calls=1,
            file_change_count=1,
            stop_reason="max_tool_iterations",
            stop_metadata={"iteration_limit": 1},
        ),
    )

    assert result.status == "incomplete"
    assert result.reason == "max tool iterations exhausted before completion"
    assert "max_tool_iterations" in (result.active_task_detail or "")


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


def test_completion_gate_marks_chinese_missing_files_as_blocked():
    intent = TaskIntentService().classify(
        "\u8acb\u53ea\u8b80\u5fc5\u8981\u7684\u5c08\u6848\u6a94\u6848\uff0c\u627e\u51fa harness profile selection \u554f\u984c"
    )
    response = "\u7d50\u679c\uff1a\u6240\u9700\u6a94\u6848\u4e0d\u5b58\u5728\uff0c\u7121\u6cd5\u7e7c\u7e8c\u3002"

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(
            content=response,
            executed_tool_calls=1,
            had_tool_error=True,
        ),
    )

    assert result.status == "blocked"
    assert result.active_task_status == "blocked"


def test_completion_gate_marks_blocker_heading_as_blocked():
    intent = TaskIntentService().classify(
        "\u8acb\u53ea\u8b80\u5fc5\u8981\u7684\u5c08\u6848\u6a94\u6848\uff0c\u627e\u51fa harness profile selection \u554f\u984c"
    )
    response = "## Blocker\n\nRequired project files were not found."

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(
            content=response,
            executed_tool_calls=1,
            had_tool_error=True,
        ),
    )

    assert result.status == "blocked"
    assert result.active_task_status == "blocked"


def test_completion_gate_marks_strong_chinese_blocker_without_tool_error():
    intent = TaskIntentService().classify(
        "\u5ef6\u7e8c\u525b\u525b\u7684\u7a0b\u5f0f\u78bc\u89c0\u5bdf\uff0c\u8acb\u8a2d\u8a08\u6700\u5c0f regression test \u6848\u4f8b\uff1b\u4e0d\u8981\u4fee\u6539\u6a94\u6848\u3001\u4e0d\u8981\u57f7\u884c\u6e2c\u8a66\u3002"
    )
    response = "\u5b8c\u6210\u72c0\u614b\uff1a\u5df2\u660e\u78ba\u963b\u64cb\uff0c\u7121\u6cd5\u7e7c\u7e8c\u3002"

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response),
    )

    assert result.status == "blocked"
    assert result.active_task_status == "blocked"


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


def test_completion_gate_completes_generic_task_with_substantive_answer():
    intent = TaskIntentService().classify("請用三句話介紹 OpenSprite，不要讀檔也不要上網。")
    answer = "OpenSprite 是一個本機 AI 助手。它可以協助處理對話、工具與任務流程。它重視 trace 與可驗證結果。"

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(content=answer),
    )

    assert result.status == "complete"
    assert result.reason == "generic task returned a response"


def test_completion_gate_completes_debug_answer_even_when_it_contains_questions():
    intent = TaskIntentService().classify("請幫我 debug Python ModuleNotFoundError 的常見原因，不要讀檔、不要上網。")
    answer = "常見原因包括套件未安裝、Python 環境不一致，以及模組路徑錯誤。可以先檢查 which python? 再確認 pip 安裝位置。"

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(content=answer),
    )

    assert result.status == "complete"
    assert result.reason == "debug diagnosis was provided without requiring code changes"


def test_completion_gate_requires_web_evidence_for_external_search_task():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = _web_contract(intent)

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="正在幫你搜尋 Reddit 搜尋相關的開源專案...",
        execution_result=ExecutionResult(content="正在幫你搜尋 Reddit 搜尋相關的開源專案...", task_contract=contract),
    )

    assert result.status == "incomplete"
    assert result.reason == "required task evidence was not produced"
    assert result.missing_evidence

def test_evidence_gate_reports_missing_contract_items():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = _web_contract(intent)

    result = EvidenceGateService().evaluate(
        task_intent=intent,
        execution_result=ExecutionResult(content="我會搜尋 Reddit。", task_contract=contract),
        verification_passed=False,
    )

    assert result.passed is False
    assert result.reason == "required task evidence was not produced"
    assert result.missing_evidence

def test_task_contract_records_web_source_acceptance_criteria():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    kinds = [criterion.kind for criterion in contract.acceptance_criteria]
    assert "source_artifact" in kinds
    assert "source_detail" in kinds
    assert "substantive_final_answer" in kinds
    assert "source_reference" in kinds
    criteria = {criterion.kind: criterion for criterion in contract.acceptance_criteria}
    assert criteria["source_artifact"].min_count == 2
    assert criteria["source_detail"].min_count == 1


def test_task_contract_requires_web_research_for_chinese_market_lookup():
    intent = TaskIntentService().classify("幫我找 2330市值")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    assert contract.task_type == "web_research"
    assert contract.allow_no_tool_final is False
    assert any(requirement.tool_group == "web_research" for requirement in contract.requirements)
    assert {criterion.kind for criterion in contract.acceptance_criteria} >= {
        "source_artifact",
        "source_detail",
        "substantive_final_answer",
        "source_reference",
    }


def test_task_contract_requires_web_research_for_high_confidence_chinese_external_lookups():
    examples = [
        "今天台北天氣",
        "美元台幣匯率",
        "00981T 即時報價",
        "幫我搜尋 NVIDIA 新聞",
        "請上網找 OpenAI 來源連結",
    ]

    for message in examples:
        intent = TaskIntentService().classify(message)
        contract = TaskContractService.build(
            task_intent=intent,
            current_message=intent.objective,
        )

        assert contract.task_type == "web_research", message
        assert contract.allow_no_tool_final is False, message
        assert any(requirement.tool_group == "web_research" for requirement in contract.requirements), message


def test_task_contract_does_not_treat_ambiguous_chinese_lookup_words_as_web_research():
    examples = [
        "查一下這個檔案",
        "查詢設定",
        "整理最新進度",
        "搜尋目前專案裡的 TODO",
        "搜索目前專案裡的 TODO",
        "查找剛剛提到的內容",
        "search the repo for TODO",
    ]

    for message in examples:
        intent = TaskIntentService().classify(message)
        contract = TaskContractService.build_deterministic(
            task_intent=intent,
            current_message=intent.objective,
        )

        assert contract.task_type != "web_research", message
        assert not any(requirement.tool_group == "web_research" for requirement in contract.requirements), message


def test_task_contract_requires_workspace_read_for_direct_repo_lookup():
    intent = TaskIntentService().classify("search the repo for auth config")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    assert contract.task_type == "workspace_read"
    assert contract.allow_no_tool_final is False
    assert any(requirement.tool_group == "workspace_read" for requirement in contract.requirements)
    assert any(criterion.kind == "substantive_final_answer" for criterion in contract.acceptance_criteria)


def test_completion_gate_requires_workspace_evidence_for_direct_repo_lookup():
    intent = TaskIntentService().classify("search the repo for auth config")
    contract = _workspace_contract(intent)

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="I will inspect the repo for auth config.",
        execution_result=ExecutionResult(content="I will inspect the repo for auth config.", task_contract=contract),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert completion.missing_evidence

def test_completion_gate_rejects_terse_workspace_answer_after_reading():
    intent = TaskIntentService().classify("請看 src/opensprite/agent/task_contract.py")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="看過了。",
        execution_result=ExecutionResult(
            content="看過了。",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="read_file", ok=True),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "assistant final answer was too terse for the task"


def test_completion_gate_requires_workspace_answer_to_reference_requested_path():
    intent = TaskIntentService().classify("請看 src/opensprite/agent/task_contract.py")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我看過這段邏輯了。它會先建立 deterministic contract，再依 task type 補 evidence requirement，"
        "最後把 acceptance criteria 一起帶進 completion gate。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="read_file", ok=True),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "assistant final answer did not reference inspected workspace context"


def test_completion_gate_completes_workspace_read_with_evidence_and_substantive_answer():
    intent = TaskIntentService().classify("請看 src/opensprite/agent/task_contract.py")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我看過 src/opensprite/agent/task_contract.py 了。這段邏輯會先建立 deterministic contract，"
        "再依 task type 補 evidence requirement，最後把 acceptance criteria 一起帶進 completion gate。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="read_file", ok=True),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_requires_workspace_location_for_where_question():
    intent = TaskIntentService().classify("auth config 在哪")
    contract = _workspace_contract(intent)
    answer = "我查到 auth config 是在設定載入流程中處理，主要由設定服務負責解析與套用。"

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="grep_files", ok=True),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "assistant final answer did not identify the workspace location"

def test_completion_gate_completes_workspace_location_answer_with_path():
    intent = TaskIntentService().classify("auth config 在哪")
    contract = _workspace_contract(intent)
    answer = (
        "我查到 auth config 相關邏輯在 src/opensprite/config/schema.py，"
        "其中 Config 與 provider settings 會處理認證與模型設定的載入。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="grep_files", ok=True),),
        ),
    )

    assert completion.status == "complete"

def test_task_contract_requires_history_retrieval_for_prior_context_lookup():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    assert contract.task_type == "history_retrieval"
    assert contract.allow_no_tool_final is False
    assert any(requirement.tool_group == "history_retrieval" for requirement in contract.requirements)
    assert any(criterion.kind == "substantive_final_answer" for criterion in contract.acceptance_criteria)


def test_completion_gate_requires_history_evidence_for_prior_context_lookup():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")
    contract = _history_contract(intent)

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="我先回頭查一下剛剛的內容。",
        execution_result=ExecutionResult(content="我先回頭查一下剛剛的內容。", task_contract=contract),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert completion.missing_evidence

def test_completion_gate_rejects_terse_history_answer_after_retrieval():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="找到了。",
        execution_result=ExecutionResult(
            content="找到了。",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "assistant final answer was too terse for the task"


def test_completion_gate_completes_history_retrieval_with_evidence_and_answer():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我回頭查過前面的內容了。剛剛提到的三個方案是：\n"
        "1. 先收斂 deterministic regex。\n"
        "2. 再補 planner contract 的安全合併。\n"
        "3. 最後把 trace observability 顯示補齊。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_requires_history_answer_to_reference_prior_context():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "三個方案是：\n"
        "1. 收斂 deterministic regex。\n"
        "2. 補 planner contract。\n"
        "3. 補 trace observability。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 2}),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "assistant final answer did not reference retrieved prior context"


def test_completion_gate_requires_enough_history_items():
    intent = TaskIntentService().classify("你剛剛提到哪三個方案")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我回頭查過前面的內容了，但目前只整理出兩個方案，還缺少第三個：\n"
        "1. 收斂 deterministic regex，避免模糊查詢直接被硬判成 web。\n"
        "2. 補 planner contract，讓不明確的請求可加上更嚴格 evidence。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 2}),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "assistant did not provide enough recalled items"


def test_completion_gate_rejects_answer_after_empty_history_retrieval():
    intent = TaskIntentService().classify("前面說過的 threshold 是多少")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "前面說過 threshold 是 0.7，這是 planner contract 的預設信心門檻，"
        "因此我會直接用這個數值作為目前設定的答案。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 0}),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "assistant answered despite empty history retrieval"


def test_completion_gate_allows_not_found_answer_after_empty_history_retrieval():
    intent = TaskIntentService().classify("前面說過的 threshold 是多少")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我查過前面的內容，但 not found matching prior threshold 討論，"
        "所以不能可靠回答數值；需要的話我可以再用目前設定檔重新查一次。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="search_history", ok=True, metadata={"result_count": 0}),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_requires_web_evidence_for_chinese_market_lookup():
    intent = TaskIntentService().classify("查一下 2330市值")
    contract = TaskContract(
        objective=intent.objective,
        task_type="web_research",
        requirements=(EvidenceRequirement(kind="tool_group", tool_group="web_research"),),
        allow_no_tool_final=False,
        contract_sources=("test",),
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="讓我查一下最新市值：",
        execution_result=ExecutionResult(content="讓我查一下最新市值：", task_contract=contract),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert completion.missing_evidence

def test_task_contract_inherits_web_research_for_short_follow_up():
    intent = TaskIntentService().classify("那00981t呢")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "幫我查 00980A 這檔 ETF 的股價和基本資料"},
            {"role": "assistant", "content": "我查到 00980A 的公開資訊來源。"},
        ],
    )

    assert contract.task_type == "web_research"
    assert contract.allow_no_tool_final is False
    assert any(requirement.tool_group == "web_research" for requirement in contract.requirements)
    assert {criterion.kind for criterion in contract.acceptance_criteria} >= {
        "source_artifact",
        "source_detail",
        "substantive_final_answer",
        "source_reference",
    }


def test_completion_gate_requires_web_evidence_for_short_follow_up():
    intent = TaskIntentService().classify("那00981t呢")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "幫我查 00980A 這檔 ETF 的股價和基本資料"},
            {"role": "assistant", "content": "我查到 00980A 的公開資訊來源。"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="## 查詢 00981T\n\n讓我搜尋一下這個代碼，請稍候。",
        execution_result=ExecutionResult(
            content="## 查詢 00981T\n\n讓我搜尋一下這個代碼，請稍候。",
            task_contract=contract,
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert completion.missing_evidence


def test_task_contract_allows_one_source_for_explicit_url_web_tasks():
    intent = TaskIntentService().classify("Please summarize https://example.com/docs")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    criteria = {criterion.kind: criterion for criterion in contract.acceptance_criteria}
    assert criteria["source_artifact"].min_count == 1
    assert criteria["source_detail"].min_count == 1


def test_completion_gate_requires_web_source_artifacts_after_evidence():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="找到幾個可能方向，包括 Reddit 搜尋工具與相關 API，可以再依需求挑選整合方式。",
        execution_result=ExecutionResult(
            content="找到幾個可能方向，包括 Reddit 搜尋工具與相關 API，可以再依需求挑選整合方式。",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task artifacts were not produced"
    assert "web_source" in (completion.active_task_detail or "")


def test_completion_gate_requires_traceable_web_source_metadata():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=(
            "我找到 Reddit 官方 API 文件與第三方搜尋方向，可以先看 reddit.com 的官方文件，"
            "再評估是否需要補充歷史資料來源。這樣能先確認授權與查詢限制，再決定整合方式。"
        ),
        execution_result=ExecutionResult(
            content="done",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
            task_artifacts=(TaskArtifact(kind="web_source", source_tool="web_search", content_preview="source"),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task artifacts were not traceable"
    assert "source metadata" in (completion.active_task_detail or "")


def test_completion_gate_rejects_search_only_web_source_artifacts():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Reddit 官方 API 文件在 reddit.com 提供搜尋與列表相關端點，Reddit API wiki 也補充整合注意事項。"
        "這些來源可先用來判斷授權、速率限制與資料保留策略，再決定是否需要第三方歷史資料。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
            task_artifacts=(_web_source_artifact(),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required source material was insufficient"


def test_completion_gate_rejects_too_short_web_fetch_source_detail():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Reddit 官方 API 文件在 reddit.com 提供搜尋與列表相關端點，Reddit API wiki 也補充整合注意事項。"
        "這些來源可先用來判斷授權、速率限制與資料保留策略，再決定是否需要第三方歷史資料。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(ToolEvidence(name="web_search", ok=True), ToolEvidence(name="web_fetch", ok=True)),
            task_artifacts=(_web_source_artifact(), _web_fetch_artifact(is_too_short=True)),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required source material was insufficient"


def test_completion_gate_rejects_failed_web_fetch_source_artifact():
    intent = TaskIntentService().classify("幫我找 2330 市值")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = "台積電市值資料來源是 Yahoo Finance。這個回答故意引用失敗 fetch，應被 gate 擋下。"

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_fetch", ok=False),),
            task_artifacts=(
                TaskArtifact(
                    kind="web_source",
                    source_tool="web_fetch",
                    ok=False,
                    content_preview="Error executing web_fetch: HTTP Error: 404 Not Found",
                    metadata={
                        "sources": [
                            {
                                "tool_name": "web_fetch",
                                "url": "https://finance.yahoo.com/quote/2330.TW/",
                                "snippet": "Error executing web_fetch: HTTP Error: 404 Not Found",
                            }
                        ]
                    },
                ),
            ),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"


def test_completion_gate_rejects_blocked_web_fetch_source_detail():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Reddit 官方 API 文件在 reddit.com 提供搜尋與列表相關端點，Reddit API wiki 也補充整合注意事項。"
        "這些來源可先用來判斷授權、速率限制與資料保留策略，再決定是否需要第三方歷史資料。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(ToolEvidence(name="web_search", ok=True), ToolEvidence(name="web_fetch", ok=True)),
            task_artifacts=(_web_source_artifact(), _web_fetch_artifact(blocked_or_challenge=True)),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required source material was insufficient"


def test_completion_gate_rejects_web_research_coverage_gaps():
    intent = TaskIntentService().classify("Please search online for current AI browser pricing.")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "AI Browser Docs at docs.test explains the official browser documentation, while AI Browser Pricing at pricing.test "
        "indicates pricing information may need separate verification before a final recommendation."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_research", ok=True),),
            task_artifacts=(_web_research_coverage_gap_artifact(),),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required source material was insufficient"
    assert "Web research coverage gap" in (completion.active_task_detail or "")
    assert "Target fetch count not met: need 2, fetched 1" in (completion.active_task_detail or "")
    assert "ai browser pricing" in (completion.active_task_detail or "")


def test_completion_gate_accepts_web_research_when_fetch_target_met_with_partial_query_gap():
    intent = TaskIntentService().classify("Please search online for current AI browser pricing.")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Two useful sources are AI Browser Docs at https://docs.test/browser and "
        "AI Browser Market at https://market.test/browser."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_research", ok=True),),
            task_artifacts=(_web_research_partial_query_artifact(),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_rejects_empty_web_research_even_when_assistant_asks_user():
    intent = TaskIntentService().classify("你再去網路上找更多 GPT Image 影片流程資料")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    response = "網路搜尋被阻擋，請提供可存取的網頁連結，我再幫你分析。"

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(
            content=response,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(
                    name="web_research",
                    ok=False,
                    metadata={
                        "source_count": 0,
                        "fetched_count": 0,
                        "coverage": {"target_fetch_count": 4, "target_met": False, "fetched_count": 0},
                    },
                ),
            ),
            task_artifacts=(),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert completion.missing_evidence


def test_completion_gate_rejects_terse_web_research_final_answer():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="找到了。",
        execution_result=ExecutionResult(
            content="找到了。",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
            task_artifacts=_web_research_artifacts(),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "assistant final answer was too terse for the task"


def test_completion_gate_requires_web_source_reference_in_final_answer():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我找到幾個可行方向：官方 API 適合授權後搜尋與抓取討論串，第三方歷史資料源可作補充，"
        "也可以用一般網頁搜尋搭配站內查詢。建議先用官方 API，若需要大量歷史查詢再評估第三方資料源。"
        "整合時要先確認授權限制、查詢速率、資料保留政策，以及是否需要補充快取與去重機制。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
            task_artifacts=_web_research_artifacts(),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "assistant final answer did not reference gathered sources"


def test_completion_gate_completes_web_research_with_source_artifact_and_answer():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "我找到幾個可行方向：Reddit 官方 API 文件在 reddit.com，適合授權後搜尋與抓取討論串，"
        "Pushshift 類資料源可作歷史資料補充，也可以用一般 web search 搭配 site:reddit.com 查詢。"
        "建議先用官方 API，若需要大量歷史查詢再評估第三方資料源。"
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_search", ok=True),),
            task_artifacts=_web_research_artifacts(),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_completes_explicit_url_with_substantive_fetch_source():
    intent = TaskIntentService().classify("Please summarize https://www.reddit.com/dev/api/")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "The Reddit API docs at reddit.com describe official API access for listings, search-adjacent endpoints, "
        "authentication, and rate-limit considerations. That is enough source material to summarize the requested URL "
        "and recommend checking auth and rate policies before implementation."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="web_fetch", ok=True),),
            task_artifacts=(_web_fetch_artifact(),),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_allows_optional_search_errors_after_successful_fetch_sources():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Reddit API docs at reddit.com describe official API access for listings, search-adjacent endpoints, "
        "authentication, and rate-limit considerations. A Reddit search guide at example.com adds practical query "
        "guidance, so these fetched sources are enough even though a broad search provider returned no results."
    )
    second_fetch_artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_fetch",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://example.com/reddit-search-guide",
                    "title": "Reddit Search Guide",
                    "snippet": "Practical guidance for searching Reddit content from web sources.",
                    "query": "https://example.com/reddit-search-guide",
                    "provider": "web_fetch",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                    "min_content_chars": 800,
                    "extractor": "trafilatura",
                    "truncated": False,
                }
            ],
            "source_count": 1,
        },
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            had_tool_error=True,
            tool_evidence=(
                ToolEvidence(name="web_search", ok=False, metadata={"error": "DuckDuckGo returned no results"}),
                ToolEvidence(name="web_fetch", ok=True),
            ),
            task_artifacts=(_web_fetch_artifact(), second_fetch_artifact),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_allows_optional_fetch_errors_after_successful_fetch_sources():
    intent = TaskIntentService().classify("Find current Qwen model releases and summarize them with sources.")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Qwen's current model line includes Qwen3, with details from qwenlm.github.io, and a recent "
        "Qwen3 checkpoint listed on Hugging Face. These successful sources are enough to answer the request "
        "even though one extra model URL returned 404."
    )
    second_fetch_artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_fetch",
        content_preview="source",
        metadata={
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507",
                    "title": "Qwen3-30B-A3B-Instruct-2507",
                    "snippet": "Model card for a recent Qwen3 instruct checkpoint.",
                    "query": "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507",
                    "provider": "web_fetch",
                    "content_chars": 1200,
                    "is_too_short": False,
                    "has_main_content": True,
                    "blocked_or_challenge": False,
                    "min_content_chars": 800,
                    "extractor": "trafilatura",
                    "truncated": False,
                }
            ],
            "source_count": 1,
        },
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=3,
            had_tool_error=True,
            tool_evidence=(
                ToolEvidence(name="web_fetch", ok=True),
                ToolEvidence(name="web_fetch", ok=False, metadata={"error": "HTTP 404"}),
            ),
            task_artifacts=(_web_fetch_artifact(), second_fetch_artifact),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_allows_optional_fetch_errors_after_web_research_fetches_sources():
    intent = TaskIntentService().classify("Find current Qwen model releases and summarize them with sources.")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = (
        "Qwen's current model line includes Qwen3, with details from qwenlm.github.io and a fetched "
        "Hugging Face model card. These sources satisfy the request even though one extra URL returned 404."
    )
    web_research_artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_research",
        content_preview="source",
        metadata={
            "coverage": {"fetched_count": 2, "target_met": True},
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "url": "https://qwenlm.github.io/blog/qwen3/",
                    "title": "Qwen3",
                    "snippet": "Qwen3 release notes and model details.",
                    "content_chars": 1200,
                    "has_main_content": True,
                    "is_too_short": False,
                    "blocked_or_challenge": False,
                },
                {
                    "tool_name": "web_fetch",
                    "url": "https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507",
                    "title": "Qwen3 model card",
                    "snippet": "Model card for a recent Qwen3 instruct checkpoint.",
                    "content_chars": 1200,
                    "has_main_content": True,
                    "is_too_short": False,
                    "blocked_or_challenge": False,
                },
            ],
            "source_count": 2,
        },
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=2,
            had_tool_error=True,
            tool_evidence=(
                ToolEvidence(name="web_research", ok=True),
                ToolEvidence(name="web_fetch", ok=False, metadata={"error": "HTTP 404"}),
            ),
            task_artifacts=(web_research_artifact,),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_still_blocks_failed_fetch_tool_errors():
    intent = TaskIntentService().classify("Please summarize https://www.reddit.com/dev/api/")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    answer = "I could not fetch reddit.com, so I cannot summarize the source."

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            had_tool_error=True,
            tool_evidence=(ToolEvidence(name="web_fetch", ok=False, metadata={"error": "HTTP 404"}),),
            task_artifacts=(),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "tool execution reported an error without a clear blocker handoff"


def test_completion_gate_accepts_browser_source_artifact_for_web_research():
    intent = TaskIntentService().classify("Open https://example.com/docs and summarize the source")
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )
    artifact = TaskArtifact(
        kind="web_source",
        source_tool="browser_navigate",
        metadata={
            "sources": [
                {
                    "url": "https://example.com/docs",
                    "title": "Example Docs",
                    "snippet": "Example browser automation documentation.",
                }
            ]
        },
    )
    answer = (
        "Example Docs at example.com says the page is documentation for browser automation. "
        "That source is enough to summarize the requested page, and no separate search result is needed."
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(
            content=answer,
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(ToolEvidence(name="browser_navigate", ok=True),),
            task_artifacts=(artifact,),
        ),
    )

    assert completion.status == "complete"


def test_completion_gate_marks_progress_only_fetch_response_incomplete():
    intent = TaskIntentService().classify("看一下 ai 版 幫我抓20 筆")
    contract = _itemized_contract(intent)

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="好，幫你抓 r/taiwan 熱門文章 20 筆！",
        execution_result=ExecutionResult(content="好，幫你抓 r/taiwan 熱門文章 20 筆！", task_contract=contract),
    )

    assert result.status == "incomplete"
    assert result.reason == "assistant did not provide the requested itemized result"

def test_quality_gate_reports_missing_requested_items():
    intent = TaskIntentService().classify("看一下 ai 版 幫我抓20 筆")
    contract = _itemized_contract(intent)

    result = QualityGateService().evaluate(
        task_intent=intent,
        response_text="好，幫你抓 r/taiwan 熱門文章 20 筆！",
        execution_result=ExecutionResult(content="好，幫你抓 r/taiwan 熱門文章 20 筆！", task_contract=contract),
    )

    assert result.passed is False
    assert result.status == "incomplete"
    assert result.reason == "assistant did not provide the requested itemized result"

def test_task_contract_records_itemized_acceptance_criterion():
    intent = TaskIntentService().classify("看一下 ai 版 幫我抓20 筆")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    assert len(contract.acceptance_criteria) == 1
    criterion = contract.acceptance_criteria[0]
    assert criterion.kind == "itemized_output"
    assert criterion.min_count == 3
    assert criterion.max_response_chars == 260
    assert contract.to_metadata()["acceptance_criteria"][0]["kind"] == "itemized_output"


def test_task_contract_ignores_numbers_inside_identifiers_for_itemized_count():
    intent = TaskIntentService().classify("Please answer in one sentence: ORCHID-924 是什麼？")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
    )

    assert not any(criterion.kind == "itemized_output" for criterion in contract.acceptance_criteria)


def test_completion_gate_accepts_one_sentence_identifier_recall():
    intent = TaskIntentService().classify("Please answer in one sentence: ORCHID-924 是什麼？")
    answer = "ORCHID-924 是你之前要我記住的代號。"

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=answer,
        execution_result=ExecutionResult(content=answer),
    )

    assert result.status == "complete"


def test_quality_gate_uses_contract_acceptance_criteria():
    intent = TaskIntentService().classify("請整理結果")
    contract = TaskContract(
        objective=intent.objective,
        task_type="task",
        acceptance_criteria=(
            AcceptanceCriterion(
                kind="itemized_output",
                min_count=3,
                max_response_chars=260,
                description="Provide at least three listed items.",
            ),
        ),
    )

    result = QualityGateService().evaluate(
        task_intent=intent,
        response_text="我會整理三筆結果。",
        execution_result=ExecutionResult(content="我會整理三筆結果。"),
        task_contract=contract,
    )

    assert result.passed is False
    assert result.reason == "assistant did not provide the requested itemized result"


def test_quality_gate_requires_verification_attempt_or_reported_gap_after_code_changes():
    intent = TaskIntentService().classify("Please fix src/app.py")
    contract = TaskContract(
        objective=intent.objective,
        task_type="code_change",
        acceptance_criteria=(
            AcceptanceCriterion(
                kind="verification_or_gap",
                description="State verification outcome or gap.",
            ),
        ),
    )

    missing_gap = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Updated src/app.py.",
        execution_result=ExecutionResult(content="Updated src/app.py.", file_change_count=1),
        task_contract=contract,
    )
    reported_gap = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Updated src/app.py. Tests not run because pytest is unavailable.",
        execution_result=ExecutionResult(content="Updated src/app.py.", file_change_count=1),
        task_contract=contract,
    )

    assert missing_gap.passed is False
    assert missing_gap.status == "needs_verification"
    assert missing_gap.reason == "verification outcome or gap was not reported"
    assert reported_gap.passed is True


def test_quality_gate_requires_operation_validation_or_risk_report():
    intent = TaskIntentService().classify("Update the MCP server configuration")
    contract = TaskContract(
        objective=intent.objective,
        task_type="operations",
        acceptance_criteria=(
            AcceptanceCriterion(kind="operation_report", description="Report validation or risk."),
        ),
    )

    missing_report = QualityGateService().evaluate(
        task_intent=intent,
        response_text="I changed the setting and finished the task.",
        execution_result=ExecutionResult(content="I changed the setting and finished the task."),
        task_contract=contract,
    )
    reported_validation = QualityGateService().evaluate(
        task_intent=intent,
        response_text="Configuration validation passed; residual risk is low.",
        execution_result=ExecutionResult(content="Configuration validation passed; residual risk is low."),
        task_contract=contract,
    )

    assert missing_report.passed is False
    assert missing_report.reason == "operation validation or risk was not reported"
    assert reported_validation.passed is True


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
    contract = _web_contract(intent)
    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="正在幫你搜尋 Reddit 搜尋相關的開源專案...",
        execution_result=ExecutionResult(content="正在幫你搜尋 Reddit 搜尋相關的開源專案...", task_contract=contract),
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content="正在幫你搜尋 Reddit 搜尋相關的開源專案...", task_contract=contract),
        attempts_used=0,
        previous_response="正在幫你搜尋 Reddit 搜尋相關的開源專案...",
    )

    assert decision.should_continue is True
    assert decision.reason == "completion_gate_incomplete"
    assert "Continue the current task" in (decision.prompt or "")
    assert "Required follow-up" in (decision.prompt or "")

def test_auto_continue_allows_first_retry_after_progress_only_fetch_response():
    intent = TaskIntentService().classify("看一下 ai 版 幫我抓20 筆")
    contract = _itemized_contract(intent)
    response = "好，幫你抓 r/taiwan 熱門文章 20 筆！"
    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=response,
        execution_result=ExecutionResult(content=response, task_contract=contract),
    )

    decision = AutoContinueService(max_auto_continues=1).decide(
        task_intent=intent,
        completion_result=completion,
        execution_result=ExecutionResult(content=response, task_contract=contract),
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
        response_text=(
            "Prompt 1: Create a clean product hero image with soft light and clear typography.\n\n"
            "Prompt 2: Render the same subject from a wider angle with consistent styling.\n\n"
            "整合版：保留共同主題、光線、構圖與文字重點，整理成可直接使用的完整 prompt。"
        ),
        execution_result=ExecutionResult(
            content=(
                "Prompt 1: Create a clean product hero image with soft light and clear typography.\n\n"
                "Prompt 2: Render the same subject from a wider angle with consistent styling.\n\n"
                "整合版：保留共同主題、光線、構圖與文字重點，整理成可直接使用的完整 prompt。"
            ),
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(
                ToolEvidence(name="ocr_image", resource_ids=("image:images/a.jpg",), ok=True),
                ToolEvidence(name="analyze_image", resource_ids=("image:images/b.jpg",), ok=True),
            ),
            task_artifacts=(
                TaskArtifact(kind="image_text", source_tool="ocr_image", resource_ids=("image:images/a.jpg",)),
                TaskArtifact(kind="image_analysis", source_tool="analyze_image", resource_ids=("image:images/b.jpg",)),
            ),
        ),
    )

    assert completion.status == "complete"
    assert completion.missing_evidence == ()


def test_completion_gate_rejects_terse_media_final_answer():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理"
    )
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/a.jpg"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="已完成。",
        execution_result=ExecutionResult(
            content="已完成。",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(name="ocr_image", resource_ids=("image:images/a.jpg",), ok=True),
            ),
            task_artifacts=(
                TaskArtifact(kind="image_text", source_tool="ocr_image", resource_ids=("image:images/a.jpg",)),
            ),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "assistant final answer was too terse for the task"


def test_completion_gate_requires_media_artifacts_after_evidence():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理"
    )
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "user", "content": "[Media-only message saved to workspace]\nImages: images/a.jpg"},
        ],
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Prompt 1: Extracted prompt details from the uploaded image with enough content to produce a usable merged output.",
        execution_result=ExecutionResult(
            content="Prompt 1: Extracted prompt details from the uploaded image with enough content to produce a usable merged output.",
            task_contract=contract,
            executed_tool_calls=1,
            tool_evidence=(
                ToolEvidence(name="ocr_image", resource_ids=("image:images/a.jpg",), ok=True),
            ),
        ),
    )

    assert completion.status == "incomplete"
    assert completion.reason == "required task artifacts were not produced"
    assert "image:images/a.jpg" in (completion.active_task_detail or "")


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
        response_text=(
            "Prompt 1: Extract the full visible prompt from the first image and preserve wording.\n"
            "Prompt 2: Extract the full visible prompt from the second image and preserve wording.\n\n"
            "整合版：合併兩張圖中的共同主題、構圖、風格與細節，移除重複段落。"
        ),
        execution_result=ExecutionResult(
            content=(
                "Prompt 1: Extract the full visible prompt from the first image and preserve wording.\n"
                "Prompt 2: Extract the full visible prompt from the second image and preserve wording.\n\n"
                "整合版：合併兩張圖中的共同主題、構圖、風格與細節，移除重複段落。"
            ),
            task_contract=contract,
            executed_tool_calls=2,
            tool_evidence=(
                ToolEvidence(name="ocr_image", resource_ids=("image_index:0",), ok=True),
                ToolEvidence(name="ocr_image", resource_ids=("image_index:1",), ok=True),
            ),
            task_artifacts=(
                TaskArtifact(kind="image_text", source_tool="ocr_image", resource_ids=("image_index:0",)),
                TaskArtifact(kind="image_text", source_tool="ocr_image", resource_ids=("image_index:1",)),
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


def test_completion_gate_respects_pure_answer_contract_over_code_intent():
    intent = TaskIntentService().classify(
        "請幫我規劃一個安全的三階段修正流程，要包含每階段驗證方式；不要呼叫工具。"
    )
    contract = TaskContract(
        objective=intent.objective,
        task_type="pure_answer",
        requirements=(),
        acceptance_criteria=(),
        allow_no_tool_final=True,
    )

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="第一階段先隔離問題，第二階段做最小修正，第三階段驗證結果。",
        execution_result=ExecutionResult(
            content="第一階段先隔離問題，第二階段做最小修正，第三階段驗證結果。",
            task_contract=contract,
        ),
    )

    assert intent.expects_code_change is True
    assert intent.expects_verification is False
    assert result.status == "complete"
    assert result.verification_required is False


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
