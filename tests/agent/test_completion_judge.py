import pytest

from opensprite.agent.completion_judge import (
    CompletionJudgeError,
    CompletionJudgeService,
    normalize_completion_judge_payload,
    parse_completion_judge_json,
)
from opensprite.config import DocumentLlmConfig
from opensprite.llms import LLMResponse


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
    with pytest.raises(CompletionJudgeError):
        normalize_completion_judge_payload({"status": "maybe", "reason": "unclear"})


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


@pytest.mark.anyio
async def test_completion_judge_service_blocks_when_llm_unconfigured():
    service = CompletionJudgeService(_llm_config())

    with pytest.raises(CompletionJudgeError):
        await service.judge(provider=None, model="unconfigured", facts={})
