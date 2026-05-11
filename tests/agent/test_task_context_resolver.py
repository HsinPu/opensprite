import asyncio

from opensprite.agent.active_task_commands import ActiveTaskCommandService
from opensprite.agent.task_contract import TaskContractService
from opensprite.agent.task_context_resolver import TaskContextDecision, TaskContextResolver
from opensprite.agent.task_intent import TaskIntentService
from opensprite.documents.active_task import create_active_task_store
from opensprite.llms.base import LLMResponse


class _Storage:
    async def get_messages(self, session_id, limit=None):
        return []

    async def get_all_sessions(self):
        return []


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


def test_task_context_uses_deterministic_follow_up_without_llm():
    provider = _FailingProvider()

    decision = asyncio.run(
        TaskContextResolver().resolve(
            current_message="那00981t呢",
            history=[
                {"role": "user", "content": "幫我查 00980A 這檔 ETF 的股價和基本資料"},
                {"role": "assistant", "content": "我查到 00980A 的公開資訊來源。"},
            ],
            task_intent=TaskIntentService().classify("那00981t呢"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == "deterministic"
    assert decision.is_follow_up is True
    assert decision.inherited_tool_group == "web_research"


def test_task_context_uses_llm_for_ambiguous_follow_up():
    provider = _JsonProvider(
        '{"is_follow_up": true, "should_inherit_active_task": false, '
        '"should_seed_active_task": false, "should_replace_active_task": false, '
        '"inherited_task_type": "web_research", "inherited_tool_group": "web_research", '
        '"confidence": 0.86, "reason": "short follow-up refers to prior external lookup"}'
    )

    decision = asyncio.run(
        TaskContextResolver().resolve(
            current_message="那這個呢",
            history=[],
            task_intent=TaskIntentService().classify("那這個呢"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm"
    assert decision.is_follow_up is True
    assert decision.inherited_task_type == "web_research"
    assert decision.inherited_tool_group == "web_research"


def test_task_context_falls_back_when_llm_json_is_invalid():
    provider = _JsonProvider("not json")

    decision = asyncio.run(
        TaskContextResolver().resolve(
            current_message="那這個呢",
            history=[],
            task_intent=TaskIntentService().classify("那這個呢"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "fallback"
    assert decision.is_follow_up is True
    assert decision.inherited_tool_group is None


def test_task_context_ignores_low_confidence_llm_decision():
    provider = _JsonProvider(
        '{"is_follow_up": true, "should_inherit_active_task": false, '
        '"should_seed_active_task": false, "should_replace_active_task": false, '
        '"inherited_task_type": "web_research", "inherited_tool_group": "web_research", '
        '"confidence": 0.2, "reason": "uncertain"}'
    )

    decision = asyncio.run(
        TaskContextResolver().resolve(
            current_message="那這個呢",
            history=[],
            task_intent=TaskIntentService().classify("那這個呢"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "fallback"
    assert decision.inherited_tool_group is None


def test_task_contract_uses_task_context_decision_for_web_follow_up():
    intent = TaskIntentService().classify("那這個呢")
    decision = TaskContextDecision(
        is_follow_up=True,
        inherited_task_type="web_research",
        inherited_tool_group="web_research",
        confidence=0.86,
        method="llm",
        reason="LLM linked the follow-up to external lookup context",
    )

    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        task_context_decision=decision,
    )

    assert contract.task_type == "web_research"
    assert contract.allow_no_tool_final is False
    assert any(requirement.tool_group == "web_research" for requirement in contract.requirements)


def test_active_task_seed_allows_llm_decision_to_replace_current_task(tmp_path):
    session_id = "telegram:room-1"
    app_home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    service = ActiveTaskCommandService(
        storage=_Storage(),
        app_home_getter=lambda: app_home,
        workspace_root_getter=lambda: workspace,
    )
    store = create_active_task_store(app_home, session_id, workspace_root=workspace)
    store.write_managed_block(
        "- Status: active\n"
        "- Goal: Refactor the agent in small safe steps.\n"
        "- Deliverable: safe refactor\n"
        "- Definition of done:\n"
        "  - tests pass\n"
        "- Constraints:\n"
        "  - none\n"
        "- Assumptions:\n"
        "  - none\n"
        "- Plan:\n"
        "  1. inspect\n"
        "- Current step: 1. inspect\n"
        "- Next step: not set\n"
        "- Completed steps:\n"
        "  - none\n"
        "- Open questions:\n"
        "  - none"
    )
    message = "好，現在請直接修掉 tests/test_app.py 的問題"
    decision = TaskContextDecision(
        should_seed_active_task=True,
        should_replace_active_task=True,
        confidence=0.86,
        method="llm",
        reason="user switched to a new concrete fix task",
    )

    asyncio.run(
        service.maybe_seed(
            session_id,
            message,
            enabled=True,
            task_intent=TaskIntentService().classify(message),
            task_context_decision=decision,
        )
    )

    updated = store.read_managed_block()
    assert f"- Goal: {message}" in updated
    seed_event = next(event for event in store.read_events() if event["event_type"] == "seed")
    assert seed_event["details"]["replace"] is True
