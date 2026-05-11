import asyncio

from opensprite.agent.completion_gate import CompletionGateService
from opensprite.agent.auto_continue import AutoContinueService
from opensprite.agent.evidence_gate import EvidenceGateService
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.quality_gate import QualityGateService
from opensprite.agent.task_artifact import TaskArtifact
from opensprite.agent.task_contract import (
    AcceptanceCriterion,
    SemanticContractClassifier,
    SemanticContractDecision,
    TaskContract,
    TaskContractService,
)
from opensprite.agent.task_intent import TaskIntentService
from opensprite.llms.base import LLMResponse
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


class _JsonProvider:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def chat(self, messages, tools=None, model=None, temperature=None, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return LLMResponse(content=self.content, model=model or "fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


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


def test_evidence_gate_reports_missing_contract_items():
    intent = TaskIntentService().classify("那幫我找找有沒有可以在reddit 搜尋的")

    result = EvidenceGateService().evaluate(
        task_intent=intent,
        execution_result=ExecutionResult(content="我會搜尋 Reddit。"),
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


def test_semantic_contract_can_add_web_research_requirement():
    intent = TaskIntentService().classify("2330 現在多少")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        semantic_decision=SemanticContractDecision(
            requires_tool_evidence=True,
            required_tool_group="web_research",
            task_type="web_research",
            allow_no_tool_final=False,
            confidence=0.86,
            reason="User asks for current stock market data.",
        ),
    )

    assert contract.task_type == "web_research"
    assert contract.allow_no_tool_final is False
    assert contract.contract_sources == ("deterministic", "semantic_classifier")
    assert contract.semantic_contract
    assert contract.semantic_contract["applied"] is True
    assert contract.semantic_contract["reason"] == "User asks for current stock market data."
    assert any(requirement.tool_group == "web_research" for requirement in contract.requirements)
    assert {criterion.kind for criterion in contract.acceptance_criteria} >= {
        "source_artifact",
        "source_detail",
        "substantive_final_answer",
        "source_reference",
    }


def test_semantic_contract_classifier_parses_web_research_decision():
    provider = _JsonProvider(
        '{"requires_tool_evidence": true, "required_tool_group": "web_research", '
        '"task_type": "web_research", "allow_no_tool_final": false, '
        '"confidence": 0.88, "reason": "Current stock price needs web evidence."}'
    )
    intent = TaskIntentService().classify("2330 現在多少")
    deterministic = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
    )

    decision = asyncio.run(
        SemanticContractClassifier().classify(
            provider=provider,
            model=provider.get_default_model(),
            task_intent=intent,
            current_message=intent.objective,
            history=[],
            deterministic_contract=deterministic,
        )
    )

    assert len(provider.calls) == 1
    assert decision is not None
    assert decision.requires_tool_evidence is True
    assert decision.required_tool_group == "web_research"
    assert decision.task_type == "web_research"
    assert decision.allow_no_tool_final is False
    assert decision.confidence == 0.88


def test_semantic_contract_classifier_skips_deterministic_requirements():
    provider = _JsonProvider("{}")
    intent = TaskIntentService().classify("Please summarize https://example.com/docs")
    deterministic = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
    )

    decision = asyncio.run(
        SemanticContractClassifier().classify(
            provider=provider,
            model=provider.get_default_model(),
            task_intent=intent,
            current_message=intent.objective,
            history=[],
            deterministic_contract=deterministic,
        )
    )

    assert decision is None
    assert provider.calls == []


def test_semantic_contract_classifier_skips_casual_conversation():
    provider = _JsonProvider("{}")
    intent = TaskIntentService().classify("hello")
    deterministic = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
    )

    decision = asyncio.run(
        SemanticContractClassifier().classify(
            provider=provider,
            model=provider.get_default_model(),
            task_intent=intent,
            current_message=intent.objective,
            history=[],
            deterministic_contract=deterministic,
        )
    )

    assert decision is None
    assert provider.calls == []


def test_semantic_contract_cannot_remove_deterministic_requirement():
    intent = TaskIntentService().classify("Please summarize https://example.com/docs")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        semantic_decision=SemanticContractDecision(
            requires_tool_evidence=False,
            allow_no_tool_final=True,
            confidence=0.95,
            reason="Incorrectly says no tool evidence is required.",
        ),
    )

    assert contract.task_type == "web_research"
    assert contract.allow_no_tool_final is False
    assert any(requirement.tool_group == "web_research" for requirement in contract.requirements)
    assert contract.semantic_contract
    assert contract.semantic_contract["applied"] is False


def test_low_confidence_semantic_contract_only_records_trace_metadata():
    intent = TaskIntentService().classify("2330 怎麼樣")

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        semantic_decision=SemanticContractDecision(
            requires_tool_evidence=True,
            required_tool_group="web_research",
            task_type="web_research",
            allow_no_tool_final=False,
            confidence=0.42,
            reason="Low confidence market lookup guess.",
        ),
    )

    assert contract.requirements == ()
    assert contract.allow_no_tool_final is True
    assert contract.semantic_contract
    assert contract.semantic_contract["applied"] is False
    assert contract.to_metadata()["semantic_contract"]["reason"] == "Low confidence market lookup guess."


def test_completion_gate_requires_web_evidence_for_chinese_market_lookup():
    intent = TaskIntentService().classify("查一下 2330市值")

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="讓我查一下最新市值：",
        execution_result=ExecutionResult(content="讓我查一下最新市值："),
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

    result = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="好，幫你抓 r/taiwan 熱門文章 20 筆！",
        execution_result=ExecutionResult(content="好，幫你抓 r/taiwan 熱門文章 20 筆！"),
    )

    assert result.status == "incomplete"
    assert result.reason == "assistant did not provide the requested itemized result"


def test_quality_gate_reports_missing_requested_items():
    intent = TaskIntentService().classify("看一下 ai 版 幫我抓20 筆")

    result = QualityGateService().evaluate(
        task_intent=intent,
        response_text="好，幫你抓 r/taiwan 熱門文章 20 筆！",
        execution_result=ExecutionResult(content="好，幫你抓 r/taiwan 熱門文章 20 筆！"),
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
