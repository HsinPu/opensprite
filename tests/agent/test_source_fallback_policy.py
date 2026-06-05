from opensprite.agent.completion_gate import CompletionGateResult
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.source_fallback_policy import (
    clean_source_fallback_snippet,
    source_fallback_allowed,
    task_contract_requires_web_sources,
)
from opensprite.agent.task_contract import AcceptanceCriterion, EvidenceRequirement, TaskContract


def test_source_fallback_policy_requires_nonfinal_web_contract():
    web_contract = TaskContract(objective="Find sources.", task_type="web_research")
    plain_contract = TaskContract(objective="Answer plainly.", task_type="pure_answer")

    assert source_fallback_allowed(
        CompletionGateResult(status="incomplete", reason="needs sources"),
        ExecutionResult(content="", task_contract=web_contract),
    )
    assert source_fallback_allowed(
        CompletionGateResult(status="blocked", reason="empty final answer"),
        ExecutionResult(content="", task_contract=web_contract),
    )
    assert source_fallback_allowed(
        CompletionGateResult(status="needs_review", reason="ungrounded citation"),
        ExecutionResult(content="", task_contract=web_contract),
    )
    assert not source_fallback_allowed(
        CompletionGateResult(status="complete", reason="answered"),
        ExecutionResult(content="", task_contract=web_contract),
    )
    assert not source_fallback_allowed(
        CompletionGateResult(status="waiting_user", reason="need user input"),
        ExecutionResult(content="", task_contract=web_contract),
    )
    assert not source_fallback_allowed(
        CompletionGateResult(status="incomplete", reason="needs sources"),
        ExecutionResult(content="", task_contract=plain_contract),
    )


def test_source_fallback_policy_detects_source_requirements_and_criteria():
    assert task_contract_requires_web_sources(
        TaskContract(
            objective="Find sources.",
            task_type="pure_answer",
            requirements=(EvidenceRequirement(kind="tool_group", tool_group="web_research"),),
        )
    )
    assert task_contract_requires_web_sources(
        TaskContract(
            objective="Cite sources.",
            task_type="pure_answer",
            acceptance_criteria=(AcceptanceCriterion(kind="source_reference"),),
        )
    )


def test_clean_source_fallback_snippet_removes_embedded_links():
    snippet = "[![](/ad.gif)](https://example.com/ad) [Docs](https://example.com/docs) keep this https://example.com/raw"

    assert clean_source_fallback_snippet(snippet) == "Docs keep this"
