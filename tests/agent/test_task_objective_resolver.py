import asyncio

from opensprite.agent.task_context_resolver import TaskContextDecision
from opensprite.agent.task_intent import TaskIntentService
from opensprite.agent.task_objective_resolver import TaskObjectiveResolver
from opensprite.llms.base import LLMResponse, UnconfiguredLLM


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


class _FailingProvider:
    async def chat(self, *args, **kwargs):
        raise AssertionError("LLM should not be called")

    def get_default_model(self) -> str:
        return "fake-model"


_WEB_RESEARCH_HISTORY = [
    {"role": "user", "content": "幫我查 00980A 這檔 ETF 的股價和基本資料"},
    {"role": "assistant", "content": "我查到 00980A 的公開資訊來源。"},
]
_FOLLOW_UP_WEB_DECISION = TaskContextDecision(
    is_follow_up=True,
    inherited_task_type="web_research",
    inherited_tool_group="web_research",
    continuation_type="follow_up",
    confidence=0.75,
    method="deterministic",
    reason="inherited web_research from recent conversation context",
)
_ACTIVE_TASK_BLOCK = (
    "- Status: active\n"
    "- Goal: Refactor the agent in small safe steps.\n"
    "- Current step: 1. inspect\n"
    "- Next step: not set"
)


def test_task_objective_resolver_enriches_short_web_follow_up():
    provider = _JsonProvider(
        '{"resolved_objective": "Research 00981T ETF price and basic public information using web sources.", '
        '"should_use_resolved_objective": true, "confidence": 0.88, '
        '"reason": "The short turn refers to the prior ETF lookup."}'
    )

    decision = asyncio.run(
        TaskObjectiveResolver().resolve(
            current_message="那00981t呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那00981t呢"),
            task_context_decision=_FOLLOW_UP_WEB_DECISION,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm"
    assert decision.should_use_resolved_objective is True
    assert decision.effective_objective == "Research 00981T ETF price and basic public information using web sources."
    assert decision.original_message == "那00981t呢"
    assert decision.to_metadata()["should_use_resolved_objective"] is True


def test_task_objective_resolver_skips_continue_active_task():
    provider = _FailingProvider()
    context = TaskContextDecision(
        is_follow_up=True,
        should_inherit_active_task=True,
        continuation_type="continue_active_task",
        confidence=0.75,
        reason="current message is a continuation of the active task",
    )

    decision = asyncio.run(
        TaskObjectiveResolver().resolve(
            current_message="繼續",
            history=[],
            task_intent=TaskIntentService().classify("繼續"),
            task_context_decision=context,
            active_task=_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == "deterministic"
    assert decision.should_use_resolved_objective is False
    assert decision.effective_objective == "繼續"


def test_task_objective_resolver_falls_back_when_provider_is_unconfigured():
    decision = asyncio.run(
        TaskObjectiveResolver().resolve(
            current_message="那00981t呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那00981t呢"),
            task_context_decision=_FOLLOW_UP_WEB_DECISION,
            provider=UnconfiguredLLM(),
            model="unconfigured",
        )
    )

    assert decision.method == "fallback"
    assert decision.should_use_resolved_objective is False
    assert decision.reason.startswith("llm unavailable")


def test_task_objective_resolver_ignores_low_confidence_objective():
    provider = _JsonProvider(
        '{"resolved_objective": "Research 00981T ETF price using web sources.", '
        '"should_use_resolved_objective": true, "confidence": 0.40, "reason": "uncertain"}'
    )

    decision = asyncio.run(
        TaskObjectiveResolver().resolve(
            current_message="那00981t呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那00981t呢"),
            task_context_decision=_FOLLOW_UP_WEB_DECISION,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "fallback"
    assert decision.should_use_resolved_objective is False


def test_task_objective_resolver_falls_back_on_invalid_json():
    provider = _JsonProvider("not json")

    decision = asyncio.run(
        TaskObjectiveResolver().resolve(
            current_message="那00981t呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那00981t呢"),
            task_context_decision=_FOLLOW_UP_WEB_DECISION,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "fallback"
    assert decision.should_use_resolved_objective is False
