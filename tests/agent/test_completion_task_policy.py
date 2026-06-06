from opensprite.agent.task_intent import (
    accepts_final_response_task_type,
    intent_supports_default_work_plan,
    intent_supports_fallback_active_task_update,
    is_analysis_response_intent_kind,
    is_generic_task_response_intent_kind,
    is_history_retrieval_task_type,
    is_media_extraction_task_type,
    is_one_turn_intent_kind,
    is_plain_answer_task_type,
    is_read_only_blocking_requirement_kind,
    is_read_only_blocking_tool_group,
    is_read_only_task_type,
    is_workspace_read_task_type,
)
from opensprite.agent.task_contract import TaskContract
from opensprite.agent.task_intent import TaskIntentService


def test_completion_task_policy_classifies_task_types():
    assert is_read_only_task_type("web_research") is True
    assert is_read_only_task_type("workspace_read") is True
    assert is_read_only_task_type("code_change") is False
    assert is_plain_answer_task_type("pure_answer") is True
    assert is_plain_answer_task_type("web_research") is False
    assert accepts_final_response_task_type("planning") is True
    assert accepts_final_response_task_type("web_research") is False
    assert is_media_extraction_task_type("media_extraction") is True
    assert is_media_extraction_task_type("web_research") is False
    assert is_history_retrieval_task_type("history_retrieval") is True
    assert is_history_retrieval_task_type("web_research") is False
    assert is_workspace_read_task_type("workspace_read") is True
    assert is_workspace_read_task_type("web_research") is False


def test_completion_task_policy_classifies_intent_kinds():
    assert is_one_turn_intent_kind("command") is True
    assert is_one_turn_intent_kind("task") is False
    assert is_analysis_response_intent_kind("analysis") is True
    assert is_analysis_response_intent_kind("task") is False
    assert is_generic_task_response_intent_kind("task") is True
    assert is_generic_task_response_intent_kind("analysis") is False


def test_completion_task_policy_classifies_read_only_blockers():
    assert is_read_only_blocking_requirement_kind("file_change") is True
    assert is_read_only_blocking_requirement_kind("verification") is True
    assert is_read_only_blocking_requirement_kind("tool_group") is False
    assert is_read_only_blocking_tool_group("execution") is True
    assert is_read_only_blocking_tool_group("workspace_write") is True
    assert is_read_only_blocking_tool_group("workspace_read") is False


def test_completion_task_policy_controls_fallback_active_task_updates():
    intent = TaskIntentService().classify("please answer")
    clarification = TaskIntentService().classify("please answer")
    object.__setattr__(clarification, "needs_clarification", True)

    assert intent_supports_fallback_active_task_update(
        intent, TaskContract(objective="x", task_type="web_research")
    ) is True
    assert intent_supports_fallback_active_task_update(
        intent, TaskContract(objective="x", task_type="pure_answer")
    ) is False
    assert intent_supports_fallback_active_task_update(
        clarification, TaskContract(objective="x", task_type="web_research")
    ) is False


def test_completion_task_policy_controls_default_work_plan_support():
    task = TaskIntentService().classify("please inspect and summarize")
    conversation = TaskIntentService().classify("")
    clarification = TaskIntentService().classify("please inspect")
    object.__setattr__(clarification, "needs_clarification", True)

    assert intent_supports_default_work_plan(task) is True
    assert intent_supports_default_work_plan(conversation) is False
    assert intent_supports_default_work_plan(clarification) is False
