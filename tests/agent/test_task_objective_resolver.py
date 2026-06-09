import asyncio

from opensprite.agent.task.contract import TaskIntent, TaskIntentService
from opensprite.agent.task.contract import (
    DETERMINISTIC_OBJECTIVE_METHOD,
    JSON_PLANNING_MIN_OUTPUT_TOKENS,
    LLM_OBJECTIVE_NOT_MORE_SPECIFIC_REASON,
    LLM_RESOLVED_TASK_OBJECTIVE_REASON,
    OBJECTIVE_ENRICHMENT_NOT_NEEDED_REASON,
    TaskContextDecision,
    TaskObjectiveResolver,
    _build_task_objective_llm_prompt as _build_llm_prompt,
)
from opensprite.config import Config
from opensprite.llms.base import LLMResponse, UnconfiguredLLM


def _resolver() -> TaskObjectiveResolver:
    return TaskObjectiveResolver(Config.load_agent_template_config().task_objective_llm)


def test_objective_prompt_preserves_tail_of_long_current_message():
    filler = "\n".join(f"背景{i}: 這是壓力測試背景，不是任務。" for i in range(60))
    message = f"{filler}\n最後一句才是任務：只回覆通關詞 EPSILON-864。"

    prompt = _build_llm_prompt(
        current_message=message,
        history=[],
        task_intent=None,
        task_context_decision=None,
        active_task="",
        work_state_summary="",
    )

    assert "背景0" in prompt
    assert "... [middle omitted] ..." in prompt
    assert "最後一句才是任務" in prompt
    assert "EPSILON-864" in prompt


class _JsonProvider:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def chat(self, messages, tools=None, model=None, max_tokens=None, **kwargs):
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
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
    continuation_type="follow_up",
    confidence=0.75,
    method=DETERMINISTIC_OBJECTIVE_METHOD,
    reason="inherited web_research from recent conversation context",
)
_ACTIVE_TASK_BLOCK = (
    "- Status: active\n"
    "- Goal: Refactor the agent in small safe steps.\n"
    "- Current step: 1. inspect\n"
    "- Next step: not set"
)
_BOUNDARY_ACTIVE_TASK_BLOCK = (
    "- Status: waiting_user\n"
    "- Goal: Refactor the agent in small safe steps.\n"
    "- Current step: 1. inspect\n"
    "- Next step: not set\n"
    "- Open questions:\n"
    "  - Reply `switch` to replace the active task (Refactor the agent in small safe steps.) "
    "with the new request (please update README), or `continue` to keep the active task."
)


def test_task_objective_reasons_are_stable():
    assert OBJECTIVE_ENRICHMENT_NOT_NEEDED_REASON == "objective enrichment not needed"
    assert LLM_RESOLVED_TASK_OBJECTIVE_REASON == "llm resolved task objective"
    assert LLM_OBJECTIVE_NOT_MORE_SPECIFIC_REASON == "llm objective was not more specific"


def test_task_objective_resolver_enriches_short_web_follow_up():
    provider = _JsonProvider(
        '{"resolved_objective": "Research 00981T ETF price and basic public information using web sources.", '
        '"should_use_resolved_objective": true, "confidence": 0.88, '
        '"reason": "The short turn refers to the prior ETF lookup."}'
    )

    llm_config = Config.load_agent_template_config().task_objective_llm.model_copy(update={"max_tokens": 322})
    decision = asyncio.run(
        TaskObjectiveResolver(llm_config).resolve(
            current_message="那00981t呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那00981t呢"),
            task_context_decision=_FOLLOW_UP_WEB_DECISION,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert provider.calls[0]["max_tokens"] == JSON_PLANNING_MIN_OUTPUT_TOKENS
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
        _resolver().resolve(
            current_message="繼續",
            history=[],
            task_intent=TaskIntentService().classify("繼續"),
            task_context_decision=context,
            active_task=_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == DETERMINISTIC_OBJECTIVE_METHOD
    assert decision.should_use_resolved_objective is False
    assert decision.effective_objective == "繼續"


def test_task_objective_resolver_skips_ambiguous_boundary_until_user_confirms():
    provider = _FailingProvider()
    context = TaskContextDecision(
        continuation_type="ambiguous_boundary",
        confidence=0.72,
        method="llm",
        reason="task boundary confidence too low; ask for confirmation",
    )

    decision = asyncio.run(
        _resolver().resolve(
            current_message="please update README",
            history=[],
            task_intent=TaskIntentService().classify("please update README"),
            task_context_decision=context,
            active_task=_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == DETERMINISTIC_OBJECTIVE_METHOD
    assert decision.should_use_resolved_objective is False
    assert decision.effective_objective == "please update README"


def test_task_objective_resolver_uses_llm_for_pending_boundary_request():
    provider = _JsonProvider(
        '{"resolved_objective": "please update README", '
        '"should_use_resolved_objective": true, "confidence": 0.9, '
        '"reason": "active task boundary prompt contains the pending request"}'
    )
    context = TaskContextDecision(
        should_seed_active_task=True,
        should_replace_active_task=True,
        continuation_type="task_switch",
        confidence=0.9,
        reason="user confirmed switching to the pending task-boundary request",
    )

    decision = asyncio.run(
        _resolver().resolve(
            current_message="switch",
            history=[],
            task_intent=TaskIntentService().classify("switch"),
            task_context_decision=context,
            active_task=_BOUNDARY_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm"
    assert decision.should_use_resolved_objective is True
    assert decision.effective_objective == "please update README"
    assert decision.original_message == "switch"


def test_task_objective_resolver_does_not_use_pending_boundary_request_without_llm():
    context = TaskContextDecision(
        should_seed_active_task=True,
        should_replace_active_task=True,
        continuation_type="task_switch",
        confidence=0.9,
        reason="user confirmed switching to the pending task-boundary request",
    )

    decision = asyncio.run(
        _resolver().resolve(
            current_message="switch",
            history=[],
            task_intent=TaskIntentService().classify("switch"),
            task_context_decision=context,
            active_task=_BOUNDARY_ACTIVE_TASK_BLOCK,
            provider=UnconfiguredLLM(),
            model="unconfigured",
        )
    )

    assert decision.method == "llm_unresolved"
    assert decision.should_use_resolved_objective is False
    assert decision.effective_objective == "switch"


def test_task_objective_resolver_uses_llm_context_for_short_new_task_even_when_intent_is_actionable():
    provider = _JsonProvider(
        '{"resolved_objective": "Fix the failing README task and summarize the change.", '
        '"should_use_resolved_objective": true, "confidence": 0.86, '
        '"reason": "task context classified the short turn as a new task"}'
    )
    intent = TaskIntent(
        kind="task",
        objective="fix it",
        done_criteria=("the task is completed",),
    )
    context = TaskContextDecision(
        is_follow_up=False,
        should_seed_active_task=True,
        should_replace_active_task=True,
        continuation_type="new_task",
        confidence=0.88,
        method="llm",
        reason="short turn starts a new task",
    )

    decision = asyncio.run(
        _resolver().resolve(
            current_message="fix it",
            history=[],
            task_intent=intent,
            task_context_decision=context,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm"
    assert decision.should_use_resolved_objective is True
    assert decision.effective_objective == "Fix the failing README task and summarize the change."


def test_task_objective_resolver_uses_recent_context_for_short_actionable_turn_without_context_decision():
    provider = _JsonProvider(
        '{"resolved_objective": "Fix the README install section using the recently discussed issue.", '
        '"should_use_resolved_objective": true, "confidence": 0.84, '
        '"reason": "recent history gives the short actionable turn enough context"}'
    )

    decision = asyncio.run(
        _resolver().resolve(
            current_message="fix it",
            history=[
                {"role": "user", "content": "The README install section points at the wrong service command."},
                {"role": "assistant", "content": "I found the incorrect install command in README.md."},
            ],
            task_intent=TaskIntentService().classify("fix it"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm"
    assert decision.should_use_resolved_objective is True
    assert decision.effective_objective == "Fix the README install section using the recently discussed issue."


def test_task_objective_resolver_stays_neutral_when_provider_is_unconfigured():
    decision = asyncio.run(
        _resolver().resolve(
            current_message="那00981t呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那00981t呢"),
            task_context_decision=_FOLLOW_UP_WEB_DECISION,
            provider=UnconfiguredLLM(),
            model="unconfigured",
        )
    )

    assert decision.method == "llm_unresolved"
    assert decision.should_use_resolved_objective is False
    assert decision.reason.startswith("llm unavailable")


def test_task_objective_resolver_stays_neutral_for_low_confidence_objective():
    provider = _JsonProvider(
        '{"resolved_objective": "Research 00981T ETF price using web sources.", '
        '"should_use_resolved_objective": true, "confidence": 0.40, "reason": "uncertain"}'
    )

    decision = asyncio.run(
        _resolver().resolve(
            current_message="那00981t呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那00981t呢"),
            task_context_decision=_FOLLOW_UP_WEB_DECISION,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm_unresolved"
    assert decision.should_use_resolved_objective is False


def test_task_objective_resolver_stays_neutral_on_invalid_json():
    provider = _JsonProvider("not json")

    decision = asyncio.run(
        _resolver().resolve(
            current_message="那00981t呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那00981t呢"),
            task_context_decision=_FOLLOW_UP_WEB_DECISION,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm_unresolved"
    assert decision.should_use_resolved_objective is False
