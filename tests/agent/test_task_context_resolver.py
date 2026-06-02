import asyncio

from opensprite.agent.active_task_commands import ActiveTaskCommandService
from opensprite.agent.completion_gate import CompletionGateService
from opensprite.agent.execution import ExecutionResult
from tests.agent.task_contract_test_helpers import TaskContractService
from opensprite.agent.task_context_resolver import TaskContextDecision, TaskContextResolver, _merge_with_deterministic
from opensprite.agent.task_intent import TaskIntentService
from opensprite.agent.task_objective_resolver import TaskObjectiveDecision
from opensprite.config import Config
from opensprite.documents.active_task import create_active_task_store
from opensprite.llms.base import LLMResponse, UnconfiguredLLM


def _resolver() -> TaskContextResolver:
    return TaskContextResolver(Config.load_agent_template_config().task_context_llm)


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


_ACTIVE_TASK_BLOCK = (
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
_BOUNDARY_ACTIVE_TASK_BLOCK = (
    "- Status: waiting_user\n"
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
    "  - Reply `switch` to replace the active task (Refactor the agent in small safe steps.) "
    "with the new request (please update README), or `continue` to keep the active task."
)
_LEGACY_BOUNDARY_ACTIVE_TASK_BLOCK = (
    "- Status: waiting_user\n"
    "- Goal: Refactor the agent in small safe steps.\n"
    "- Open questions:\n"
    "  - Confirm whether to switch from the active task (Refactor the agent in small safe steps.) "
    "to the new request (please update README), or continue the active task."
)
_WEB_RESEARCH_HISTORY = [
    {"role": "user", "content": "幫我查 00980A 這檔 ETF 的股價和基本資料"},
    {"role": "assistant", "content": "我查到 00980A 的公開資訊來源。"},
]


def test_task_context_does_not_infer_follow_up_when_llm_fails():
    provider = _FailingProvider()

    llm_config = Config.load_agent_template_config().task_context_llm.model_copy(
        update={"temperature": 0.2, "max_tokens": 321}
    )
    decision = asyncio.run(
        TaskContextResolver(llm_config).resolve(
            current_message="那00981t呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那00981t呢"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == "llm_unresolved"
    assert decision.is_follow_up is False
    assert decision.inherited_tool_group is None
    assert decision.continuation_type == "none"


def test_task_context_uses_llm_for_ambiguous_follow_up():
    provider = _JsonProvider(
        '{"is_follow_up": true, "should_inherit_active_task": false, '
        '"should_seed_active_task": false, "should_replace_active_task": false, '
        '"inherited_task_type": "web_research", "inherited_tool_group": "web_research", '
        '"confidence": 0.86, "reason": "short follow-up refers to prior external lookup"}'
    )

    decision = asyncio.run(
        _resolver().resolve(
            current_message="那這個呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那這個呢"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    task_context_config = Config.load_agent_template_config().task_context_llm
    assert provider.calls[0]["temperature"] == task_context_config.temperature
    assert provider.calls[0]["max_tokens"] == task_context_config.max_tokens
    assert decision.method == "llm"
    assert decision.is_follow_up is True
    assert decision.inherited_task_type == "web_research"
    assert decision.inherited_tool_group == "web_research"
    assert decision.continuation_type == "follow_up"


def test_task_context_uses_llm_for_short_recent_context_without_follow_up_marker():
    provider = _JsonProvider(
        '{"is_follow_up": true, "should_inherit_active_task": false, '
        '"should_seed_active_task": false, "should_replace_active_task": false, '
        '"inherited_task_type": "web_research", "inherited_tool_group": "web_research", '
        '"confidence": 0.84, "reason": "short entity query depends on prior web lookup"}'
    )

    decision = asyncio.run(
        _resolver().resolve(
            current_message="00981T price",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("00981T price"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm"
    assert decision.is_follow_up is True
    assert decision.inherited_task_type == "web_research"
    assert decision.inherited_tool_group == "web_research"


def test_task_context_does_not_backfill_llm_tool_inheritance_from_regex():
    deterministic = TaskContextDecision(
        is_follow_up=True,
        inherited_task_type="web_research",
        inherited_tool_group="web_research",
        continuation_type="follow_up",
        confidence=0.62,
        reason="regex guessed prior web context",
    )
    llm_decision = TaskContextDecision(
        is_follow_up=True,
        continuation_type="follow_up",
        confidence=0.86,
        method="llm",
        reason="follow-up is conversational, not another lookup",
    )

    decision = _merge_with_deterministic(deterministic, llm_decision)

    assert decision.method == "llm"
    assert decision.is_follow_up is True
    assert decision.inherited_task_type is None
    assert decision.inherited_tool_group is None
    assert decision.continuation_type == "follow_up"


def test_task_context_uses_llm_for_multilingual_and_typo_follow_ups():
    messages = ["¿y este?", "et celui-ci ?", "und das?", "ths one?"]

    for message in messages:
        provider = _JsonProvider(
            '{"continuation_type": "follow_up", "is_follow_up": true, '
            '"should_inherit_active_task": false, '
            '"should_seed_active_task": false, "should_replace_active_task": false, '
            '"inherited_task_type": "web_research", "inherited_tool_group": "web_research", '
            '"confidence": 0.86, "reason": "short multilingual follow-up refers to prior lookup"}'
        )

        decision = asyncio.run(
            _resolver().resolve(
                current_message=message,
                history=_WEB_RESEARCH_HISTORY,
                task_intent=TaskIntentService().classify(message),
                provider=provider,
                model=provider.get_default_model(),
            )
        )

        assert len(provider.calls) == 1
        assert decision.method == "llm"
        assert decision.is_follow_up is True
        assert decision.inherited_task_type == "web_research"
        assert decision.inherited_tool_group == "web_research"
        assert decision.continuation_type == "follow_up"


def test_task_context_does_not_infer_typo_follow_up_when_llm_fails():
    provider = _FailingProvider()

    decision = asyncio.run(
        _resolver().resolve(
            current_message="這ㄍ呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("這ㄍ呢"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == "llm_unresolved"
    assert decision.is_follow_up is False
    assert decision.inherited_tool_group is None
    assert decision.continuation_type == "none"


def test_task_context_acknowledgement_skips_llm():
    provider = _FailingProvider()

    decision = asyncio.run(
        _resolver().resolve(
            current_message="thanks",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("thanks"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == "deterministic"
    assert decision.is_follow_up is False
    assert decision.continuation_type == "ack"


def test_task_context_stays_neutral_when_llm_json_is_invalid():
    provider = _JsonProvider("not json")

    decision = asyncio.run(
        _resolver().resolve(
            current_message="那這個呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那這個呢"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm_unresolved"
    assert decision.is_follow_up is False
    assert decision.inherited_tool_group is None
    assert decision.continuation_type == "none"


def test_task_context_stays_neutral_for_low_confidence_llm_decision():
    provider = _JsonProvider(
        '{"is_follow_up": true, "should_inherit_active_task": false, '
        '"should_seed_active_task": false, "should_replace_active_task": false, '
        '"inherited_task_type": "web_research", "inherited_tool_group": "web_research", '
        '"confidence": 0.2, "reason": "uncertain"}'
    )

    decision = asyncio.run(
        _resolver().resolve(
            current_message="那這個呢",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("那這個呢"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm_unresolved"
    assert decision.inherited_tool_group is None
    assert decision.continuation_type == "none"


def test_task_context_uses_llm_for_ambiguous_active_task_replacement():
    provider = _JsonProvider(
        '{"is_follow_up": false, "should_inherit_active_task": false, '
        '"should_seed_active_task": true, "should_replace_active_task": true, '
        '"inherited_task_type": null, "inherited_tool_group": null, '
        '"confidence": 0.83, "reason": "new concrete task should replace current task"}'
    )
    message = "好，現在請直接修掉 tests/test_app.py 的問題"

    decision = asyncio.run(
        _resolver().resolve(
            current_message=message,
            history=[],
            task_intent=TaskIntentService().classify(message),
            active_task=_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm"
    assert decision.should_seed_active_task is True
    assert decision.should_replace_active_task is True


def test_task_context_downgrades_low_confidence_task_switch_to_ambiguous_boundary():
    provider = _JsonProvider(
        '{"continuation_type": "new_task", "is_follow_up": false, '
        '"should_inherit_active_task": false, '
        '"should_seed_active_task": true, "should_replace_active_task": true, '
        '"inherited_task_type": null, "inherited_tool_group": null, '
        '"confidence": 0.72, "reason": "might be a new README task"}'
    )
    message = "please update README"

    decision = asyncio.run(
        _resolver().resolve(
            current_message=message,
            history=[],
            task_intent=TaskIntentService().classify(message),
            active_task=_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm"
    assert decision.continuation_type == "ambiguous_boundary"
    assert decision.should_seed_active_task is False
    assert decision.should_replace_active_task is False
    assert decision.should_inherit_active_task is False
    assert decision.is_follow_up is False
    assert "ask for confirmation" in decision.reason


def test_task_context_consults_llm_for_active_task_boundary_without_seed_intent():
    provider = _JsonProvider(
        '{"continuation_type": "ambiguous_boundary", "is_follow_up": false, '
        '"should_inherit_active_task": false, '
        '"should_seed_active_task": false, "should_replace_active_task": false, '
        '"inherited_task_type": null, "inherited_tool_group": null, '
        '"confidence": 0.81, "reason": "could be a new README request or a follow-up"}'
    )
    message = "what about README?"

    decision = asyncio.run(
        _resolver().resolve(
            current_message=message,
            history=[],
            task_intent=TaskIntentService().classify(message),
            active_task=_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm"
    assert decision.continuation_type == "ambiguous_boundary"


def test_task_context_clears_inherited_context_for_direct_ambiguous_boundary():
    provider = _JsonProvider(
        '{"continuation_type": "ambiguous_boundary", "is_follow_up": true, '
        '"should_inherit_active_task": true, '
        '"should_seed_active_task": true, "should_replace_active_task": true, '
        '"inherited_task_type": "web_research", "inherited_tool_group": "web_research", '
        '"confidence": 0.82, "reason": "could be either a new task or continuation"}'
    )
    message = "please update README"

    decision = asyncio.run(
        _resolver().resolve(
            current_message=message,
            history=[],
            task_intent=TaskIntentService().classify(message),
            active_task=_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.continuation_type == "ambiguous_boundary"
    assert decision.is_follow_up is False
    assert decision.should_seed_active_task is False
    assert decision.should_replace_active_task is False
    assert decision.should_inherit_active_task is False
    assert decision.inherited_task_type is None
    assert decision.inherited_tool_group is None


def test_task_context_stays_neutral_when_provider_is_unconfigured():
    message = "那這個呢"

    decision = asyncio.run(
        _resolver().resolve(
            current_message=message,
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify(message),
            provider=UnconfiguredLLM(),
            model="unconfigured",
        )
    )

    assert decision.method == "llm_unresolved"
    assert decision.reason.startswith("llm unavailable")


def test_task_context_continues_active_task_without_llm():
    provider = _FailingProvider()

    decision = asyncio.run(
        _resolver().resolve(
            current_message="繼續",
            history=[],
            task_intent=TaskIntentService().classify("繼續"),
            active_task=_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == "deterministic"
    assert decision.is_follow_up is True
    assert decision.should_inherit_active_task is True
    assert decision.continuation_type == "continue_active_task"


def test_task_context_confirms_pending_boundary_switch_without_llm():
    provider = _FailingProvider()

    decision = asyncio.run(
        _resolver().resolve(
            current_message="switch",
            history=[],
            task_intent=TaskIntentService().classify("switch"),
            active_task=_BOUNDARY_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == "deterministic"
    assert decision.continuation_type == "task_switch"
    assert decision.should_seed_active_task is True
    assert decision.should_replace_active_task is True


def test_task_context_confirms_pending_boundary_continue_without_llm():
    provider = _FailingProvider()

    decision = asyncio.run(
        _resolver().resolve(
            current_message="continue",
            history=[],
            task_intent=TaskIntentService().classify("continue"),
            active_task=_BOUNDARY_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == "deterministic"
    assert decision.continuation_type == "continue_active_task"
    assert decision.should_inherit_active_task is True


def test_task_context_supports_legacy_boundary_question_format():
    provider = _FailingProvider()

    decision = asyncio.run(
        _resolver().resolve(
            current_message="switch",
            history=[],
            task_intent=TaskIntentService().classify("switch"),
            active_task=_LEGACY_BOUNDARY_ACTIVE_TASK_BLOCK,
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.continuation_type == "task_switch"
    assert decision.should_replace_active_task is True


def test_task_context_continue_without_active_task_stays_neutral_without_llm():
    provider = _FailingProvider()

    decision = asyncio.run(
        _resolver().resolve(
            current_message="繼續",
            history=_WEB_RESEARCH_HISTORY,
            task_intent=TaskIntentService().classify("繼續"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == "llm_unresolved"
    assert decision.is_follow_up is False
    assert decision.inherited_task_type is None
    assert decision.inherited_tool_group is None
    assert decision.continuation_type == "none"


def test_task_context_stays_neutral_for_recent_workspace_tool_context_without_llm():
    provider = _FailingProvider()

    decision = asyncio.run(
        _resolver().resolve(
            current_message="那這個呢",
            history=[
                {"role": "tool", "tool_name": "read_file", "content": "src/opensprite/agent/task_contract.py"},
                {"role": "assistant", "content": "我看過 task_contract.py 的邏輯。"},
            ],
            task_intent=TaskIntentService().classify("那這個呢"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == "llm_unresolved"
    assert decision.is_follow_up is False
    assert decision.inherited_task_type is None
    assert decision.inherited_tool_group is None
    assert decision.continuation_type == "none"


def test_task_context_does_not_inherit_workspace_for_standalone_error_question():
    provider = _JsonProvider(
        '{"continuation_type": "none", "is_follow_up": false, '
        '"should_inherit_active_task": false, '
        '"inherited_task_type": null, "inherited_tool_group": null, '
        '"confidence": 0.9, "reason": "standalone explanatory question"}'
    )

    decision = asyncio.run(
        _resolver().resolve(
            current_message="What can cause Connection timed out?",
            history=[
                {"role": "tool", "tool_name": "read_file", "content": "src/opensprite/agent/task_contract.py"},
                {"role": "assistant", "content": "I inspected task_contract.py."},
            ],
            task_intent=TaskIntentService().classify("What can cause Connection timed out?"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm"
    assert decision.is_follow_up is False
    assert decision.inherited_tool_group is None
    assert decision.continuation_type == "none"


def test_task_context_does_not_inherit_workspace_for_unpasted_program_request():
    provider = _JsonProvider(
        '{"is_follow_up": true, "inherited_task_type": "workspace_read", '
        '"inherited_tool_group": "workspace_read", "confidence": 0.9}'
    )

    message = "I have a Python script but have not pasted it yet. What info should I prepare?"
    decision = asyncio.run(
        _resolver().resolve(
            current_message=message,
            history=[
                {"role": "assistant", "content": "I can help write code, inspect files, and run tests."},
            ],
            task_intent=TaskIntentService().classify(message),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert provider.calls == []
    assert decision.is_follow_up is False
    assert decision.inherited_tool_group is None
    assert decision.continuation_type == "none"


def test_task_context_stays_neutral_for_recent_history_tool_context_without_llm():
    provider = _FailingProvider()

    decision = asyncio.run(
        _resolver().resolve(
            current_message="那這個呢",
            history=[
                {"role": "tool", "tool_name": "search_history", "content": "matched prior discussion about thresholds"},
                {"role": "assistant", "content": "我找到前面討論 threshold 的段落。"},
            ],
            task_intent=TaskIntentService().classify("那這個呢"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert decision.method == "llm_unresolved"
    assert decision.is_follow_up is False
    assert decision.inherited_task_type is None
    assert decision.inherited_tool_group is None
    assert decision.continuation_type == "none"


def test_task_context_accepts_llm_history_retrieval_task_type():
    provider = _JsonProvider(
        '{"continuation_type": "follow_up", "is_follow_up": true, '
        '"should_inherit_active_task": false, '
        '"should_seed_active_task": false, "should_replace_active_task": false, '
        '"inherited_task_type": "history_retrieval", "inherited_tool_group": "history_retrieval", '
        '"confidence": 0.88, "reason": "latest turn asks about prior conversation"}'
    )

    decision = asyncio.run(
        _resolver().resolve(
            current_message="and this one?",
            history=[
                {"role": "tool", "tool_name": "search_history", "content": "matched prior discussion"},
                {"role": "assistant", "content": "I found that earlier discussion."},
            ],
            task_intent=TaskIntentService().classify("and this one?"),
            provider=provider,
            model=provider.get_default_model(),
        )
    )

    assert len(provider.calls) == 1
    assert decision.method == "llm"
    assert decision.inherited_task_type == "history_retrieval"
    assert decision.inherited_tool_group == "history_retrieval"


def test_task_contract_uses_task_context_decision_for_web_follow_up():
    intent = TaskIntentService().classify("那這個呢")
    decision = TaskContextDecision(
        is_follow_up=True,
        inherited_task_type="web_research",
        inherited_tool_group="web_research",
        continuation_type="follow_up",
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


def test_completion_gate_requires_evidence_for_llm_web_follow_up():
    intent = TaskIntentService().classify("¿y este?")
    decision = TaskContextDecision(
        is_follow_up=True,
        inherited_task_type="web_research",
        inherited_tool_group="web_research",
        continuation_type="follow_up",
        confidence=0.86,
        method="llm",
        reason="short follow-up refers to prior external lookup",
    )
    contract = TaskContractService.build(
        task_intent=intent,
        current_message=intent.objective,
        task_context_decision=decision,
    )

    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text="Let me check that next.",
        execution_result=ExecutionResult(content="Let me check that next.", task_contract=contract),
    )

    assert contract.allow_no_tool_final is False
    assert completion.status == "incomplete"
    assert completion.reason == "required task evidence was not produced"
    assert completion.missing_evidence


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
    store.write_managed_block(_ACTIVE_TASK_BLOCK)
    message = "好，現在請直接修掉 tests/test_app.py 的問題"
    decision = TaskContextDecision(
        should_seed_active_task=True,
        should_replace_active_task=True,
        continuation_type="task_switch",
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


def test_active_task_seed_respects_llm_decision_not_to_seed_new_task(tmp_path):
    session_id = "telegram:room-1"
    app_home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    service = ActiveTaskCommandService(
        storage=_Storage(),
        app_home_getter=lambda: app_home,
        workspace_root_getter=lambda: workspace,
    )
    store = create_active_task_store(app_home, session_id, workspace_root=workspace)
    message = "好，現在請直接修掉 tests/test_app.py 的問題"

    asyncio.run(
        service.maybe_seed(
            session_id,
            message,
            enabled=True,
            task_intent=TaskIntentService().classify(message),
            task_context_decision=TaskContextDecision(
                is_follow_up=False,
                should_seed_active_task=False,
                should_replace_active_task=False,
                continuation_type="none",
                confidence=0.91,
                method="llm",
                reason="planner classified this as a simple no-task answer",
            ),
        )
    )

    assert store.read_status() == "inactive"
    assert not any(event["event_type"] == "seed" for event in store.read_events())


def test_active_task_seed_uses_enriched_objective_for_short_follow_up(tmp_path):
    session_id = "telegram:room-1"
    app_home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    service = ActiveTaskCommandService(
        storage=_Storage(),
        app_home_getter=lambda: app_home,
        workspace_root_getter=lambda: workspace,
    )
    store = create_active_task_store(app_home, session_id, workspace_root=workspace)
    objective = TaskObjectiveDecision(
        original_message="那00981t呢",
        resolved_objective="Research 00981T ETF price and basic public information using web sources.",
        should_use_resolved_objective=True,
        confidence=0.88,
        method="llm",
        reason="The short turn refers to the prior ETF lookup.",
    )

    asyncio.run(
        service.maybe_seed(
            session_id,
            "那00981t呢",
            enabled=True,
            task_intent=TaskIntentService().classify("那00981t呢"),
            task_context_decision=TaskContextDecision(
                is_follow_up=True,
                inherited_task_type="web_research",
                inherited_tool_group="web_research",
                continuation_type="follow_up",
                confidence=0.75,
            ),
            task_objective_decision=objective,
        )
    )

    updated = store.read_managed_block()
    assert "- Goal: Research 00981T ETF price and basic public information using web sources." in updated
    assert "  - Original user message: 那00981t呢" in updated
    seed_event = next(event for event in store.read_events() if event["event_type"] == "seed")
    assert seed_event["details"]["original_message"] == "那00981t呢"
    assert seed_event["details"]["resolved_objective"] == objective.resolved_objective


def test_active_task_seed_skips_continuation_of_current_task(tmp_path):
    session_id = "telegram:room-1"
    app_home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    service = ActiveTaskCommandService(
        storage=_Storage(),
        app_home_getter=lambda: app_home,
        workspace_root_getter=lambda: workspace,
    )
    store = create_active_task_store(app_home, session_id, workspace_root=workspace)
    store.write_managed_block(_ACTIVE_TASK_BLOCK)
    decision = TaskContextDecision(
        is_follow_up=True,
        should_inherit_active_task=True,
        continuation_type="continue_active_task",
        confidence=0.75,
        method="deterministic",
        reason="current message is a continuation of the active task",
    )

    asyncio.run(
        service.maybe_seed(
            session_id,
            "繼續",
            enabled=True,
            task_intent=TaskIntentService().classify("繼續"),
            task_context_decision=decision,
        )
    )

    assert store.read_managed_block() == _ACTIVE_TASK_BLOCK
    assert not any(event["event_type"] == "seed" for event in store.read_events())


def test_active_task_seed_marks_ambiguous_boundary_waiting_user(tmp_path):
    session_id = "telegram:room-1"
    app_home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    service = ActiveTaskCommandService(
        storage=_Storage(),
        app_home_getter=lambda: app_home,
        workspace_root_getter=lambda: workspace,
    )
    store = create_active_task_store(app_home, session_id, workspace_root=workspace)
    store.write_managed_block(_ACTIVE_TASK_BLOCK)
    message = "please update README"
    decision = TaskContextDecision(
        continuation_type="ambiguous_boundary",
        confidence=0.72,
        method="llm",
        reason="task boundary confidence too low; ask for confirmation",
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
    assert "- Status: waiting_user" in updated
    assert "- Goal: Refactor the agent in small safe steps." in updated
    assert "Reply `switch` to replace the active task" in updated
    assert "`continue` to keep the active task" in updated
    assert message in updated
    assert not any(event["event_type"] == "seed" for event in store.read_events())
    boundary_event = next(event for event in store.read_events() if event["event_type"] == "task_boundary_confirmation")
    assert boundary_event["details"]["confidence"] == 0.72


def test_active_task_seed_reactivates_confirmed_boundary_continue(tmp_path):
    session_id = "telegram:room-1"
    app_home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    service = ActiveTaskCommandService(
        storage=_Storage(),
        app_home_getter=lambda: app_home,
        workspace_root_getter=lambda: workspace,
    )
    store = create_active_task_store(app_home, session_id, workspace_root=workspace)
    store.write_managed_block(_BOUNDARY_ACTIVE_TASK_BLOCK)
    decision = TaskContextDecision(
        is_follow_up=True,
        should_inherit_active_task=True,
        continuation_type="continue_active_task",
        confidence=0.9,
        method="deterministic",
        reason="user confirmed continuing the active task after task-boundary prompt",
    )

    asyncio.run(
        service.maybe_seed(
            session_id,
            "continue",
            enabled=True,
            task_intent=TaskIntentService().classify("continue"),
            task_context_decision=decision,
        )
    )

    updated = store.read_managed_block()
    assert "- Status: active" in updated
    assert "- Goal: Refactor the agent in small safe steps." in updated
    assert "- Open questions:\n  - none" in updated
    assert "Reply `switch` to replace the active task" not in updated
    resolved_event = next(event for event in store.read_events() if event["event_type"] == "task_boundary_confirmation_resolved")
    assert resolved_event["details"]["action"] == "continue"
