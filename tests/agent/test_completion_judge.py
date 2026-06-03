import pytest

from opensprite.agent.completion_judge import (
    CompletionJudgeError,
    CompletionJudgeService,
    CompletionJudgeVerdict,
    build_completion_judge_facts,
    completion_judge_unsupported_status_reason,
    normalize_completion_judge_payload,
    parse_completion_judge_json,
)
from opensprite.agent.completion_gate import CompletionGateService
from opensprite.agent.execution import ExecutionResult, LlmStepEvent
from opensprite.agent.task_artifact import TaskArtifact
from opensprite.agent.task_contract import AcceptanceCriterion, EvidenceRequirement, TaskContract
from opensprite.agent.task_intent import TaskIntent
from opensprite.config import DocumentLlmConfig
from opensprite.llms import LLMResponse
from opensprite.tools.evidence import ToolEvidence


def _llm_config() -> DocumentLlmConfig:
    return DocumentLlmConfig(
        pass_decoding_params=True,
        temperature=0,
        max_tokens=700,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
    )


class FakeProvider:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return LLMResponse(content=self.response, model=str(kwargs.get("model") or "test-model"))


class FakeJudgeService:
    def __init__(self, verdict=None, error=None):
        self.verdict = verdict
        self.error = error
        self.calls = []

    async def judge(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.verdict


def test_parse_completion_judge_json_accepts_fenced_object():
    payload = parse_completion_judge_json(
        '```json\n{"status":"complete","reason":"answered the question"}\n```'
    )

    assert payload["status"] == "complete"
    assert payload["reason"] == "answered the question"


def test_parse_completion_judge_json_rejects_invalid_json():
    with pytest.raises(CompletionJudgeError):
        parse_completion_judge_json("not json")


def test_normalize_completion_judge_payload_validates_status():
    with pytest.raises(CompletionJudgeError) as exc_info:
        normalize_completion_judge_payload({"status": "maybe", "reason": "unclear"})
    assert str(exc_info.value) == completion_judge_unsupported_status_reason("maybe")


def test_normalize_completion_judge_payload_requires_reason():
    with pytest.raises(CompletionJudgeError):
        normalize_completion_judge_payload({"status": "complete"})


def test_normalize_completion_judge_payload_coerces_optional_fields():
    verdict = normalize_completion_judge_payload(
        {
            "status": "needs_review",
            "reason": "review required",
            "missing_evidence": ["review result"],
            "verification_required": 1,
            "review_prompt_types": ["code-reviewer"],
            "review_finding_count": "2",
            "verification_pytest_args": ["tests/agent"],
        },
        raw_response="raw",
    )

    assert verdict.status == "needs_review"
    assert verdict.verification_required is True
    assert verdict.review_prompt_types == ("code-reviewer",)
    assert verdict.review_finding_count == 2
    assert verdict.verification_pytest_args == ("tests/agent",)
    assert verdict.missing_evidence == ("review result",)
    assert verdict.raw_response_preview == "raw"
    assert verdict.metadata["method"] == "llm"


@pytest.mark.anyio
async def test_completion_judge_service_calls_provider_with_decoding_config():
    provider = FakeProvider('{"status":"incomplete","reason":"missing source citation"}')
    service = CompletionJudgeService(_llm_config())

    verdict = await service.judge(
        provider=provider,
        model="test-model",
        facts={"response": "done"},
    )

    assert verdict.status == "incomplete"
    assert verdict.reason == "missing source citation"
    assert provider.calls
    messages, kwargs = provider.calls[0]
    assert kwargs["model"] == "test-model"
    assert kwargs["temperature"] == 0
    assert kwargs["max_tokens"] == 700
    assert messages[0].role == "system"
    assert messages[1].role == "user"
    user_prompt = messages[1].content
    assert "Judge this semantically across languages" in user_prompt
    assert "exact phrase matching" in user_prompt
    assert "search, fetch" not in user_prompt


@pytest.mark.anyio
async def test_completion_judge_service_blocks_when_llm_unconfigured():
    service = CompletionJudgeService(_llm_config())

    with pytest.raises(CompletionJudgeError):
        await service.judge(provider=None, model="unconfigured", facts={})


def test_build_completion_judge_facts_uses_structured_execution_data():
    intent = TaskIntent(
        kind="task",
        objective="Find current sources",
        constraints=("cite sources",),
        done_criteria=("include URLs",),
        long_running=True,
    )
    contract = TaskContract(
        objective="Find current sources",
        task_type="web_research",
        requirements=(EvidenceRequirement(kind="web_source", min_count=2, description="sources"),),
        acceptance_criteria=(AcceptanceCriterion(kind="source_grounded_final_answer", min_count=2),),
    )
    result = ExecutionResult(
        content="answer",
        executed_tool_calls=2,
        touched_paths=("README.md",),
        verification_attempted=True,
        task_contract=contract,
        tool_evidence=(
            ToolEvidence(
                name="web_search",
                ok=True,
                result_preview="source preview",
                metadata={"sources": [{"url": "https://example.com", "title": "Example"}]},
            ),
        ),
        task_artifacts=(
            TaskArtifact(
                kind="web_source",
                source_tool="web_search",
                content_preview="artifact preview",
                metadata={"source_count": 1},
            ),
        ),
        llm_step_events=[
            LlmStepEvent(
                iteration=1,
                attempt=1,
                status="success",
                provider="openrouter",
                model="model",
                duration_ms=50,
                estimated_input_tokens=10,
                message_tokens=8,
                tool_schema_tokens=2,
                tools_enabled=True,
                tool_count=3,
                tool_calls=1,
            )
        ],
    )

    facts = build_completion_judge_facts(
        task_intent=intent,
        response_text="final response",
        execution_result=result,
    )

    assert facts["task_intent"]["objective"] == "Find current sources"
    assert facts["task_contract"]["task_type"] == "web_research"
    assert facts["assistant_response"]["text"] == "final response"
    assert facts["execution"]["executed_tool_calls"] == 2
    assert facts["execution"]["verification_attempted"] is True
    assert facts["tool_evidence"][0]["name"] == "web_search"
    assert facts["task_artifacts"][0]["kind"] == "web_source"
    assert facts["llm_steps"][0]["tool_calls"] == 1


@pytest.mark.anyio
async def test_completion_gate_evaluate_with_judge_returns_judge_verdict():
    intent = TaskIntent(kind="task", objective="answer")
    verdict = CompletionJudgeVerdict(
        status="complete",
        reason="judge says done",
        active_task_status="done",
        verification_required=True,
        verification_attempted=True,
        verification_passed=True,
    )
    judge = FakeJudgeService(verdict=verdict)
    service = CompletionGateService(llm_config=_llm_config(), judge_service=judge)

    result = await service.evaluate_with_judge(
        task_intent=intent,
        response_text="answer",
        execution_result=ExecutionResult(content="answer"),
        provider=object(),
        model="model",
    )

    assert result.status == "complete"
    assert result.reason == "judge says done"
    assert result.active_task_status == "done"
    assert result.verification_passed is True
    assert judge.calls[0]["facts"]["assistant_response"]["text"] == "answer"


@pytest.mark.anyio
async def test_completion_gate_evaluate_with_judge_blocks_on_judge_error():
    intent = TaskIntent(kind="task", objective="answer")
    judge = FakeJudgeService(error=CompletionJudgeError("bad judge"))
    service = CompletionGateService(llm_config=_llm_config(), judge_service=judge)

    result = await service.evaluate_with_judge(
        task_intent=intent,
        response_text="answer",
        execution_result=ExecutionResult(content="answer"),
        provider=object(),
        model="model",
    )

    assert result.status == "blocked"
    assert result.reason == "bad judge"
    assert result.active_task_status == "blocked"
