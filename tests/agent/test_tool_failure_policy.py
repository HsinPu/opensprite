from opensprite.agent.execution import ExecutionResult
from opensprite.agent.execution_support.artifacts import TaskArtifact
from opensprite.agent.completion_gate import (
    has_only_optional_history_retrieval_failures,
    has_only_optional_web_discovery_failures,
    has_only_optional_workspace_discovery_failures,
    has_successful_fetched_web_source_artifact,
    is_optional_workspace_batch_failure_tool,
)
from opensprite.tools.evidence import ToolEvidence
from opensprite.tools.evidence import is_web_discovery_tool, is_web_fetch_source_record_tool


def _evidence(name: str, *, ok: bool, metadata: dict | None = None) -> ToolEvidence:
    return ToolEvidence(name=name, ok=ok, result_preview="", metadata=metadata or {})


def test_tool_failure_policy_classifies_optional_failure_tools():
    assert is_web_discovery_tool("web_search") is True
    assert is_web_discovery_tool("web_fetch") is False
    assert is_web_fetch_source_record_tool("web_fetch") is True
    assert is_optional_workspace_batch_failure_tool("batch") is True
    assert is_optional_workspace_batch_failure_tool("read_file") is False


def test_tool_failure_policy_allows_optional_web_discovery_failure_after_fetch_source():
    result = ExecutionResult(
        content="done",
        tool_evidence=(
            _evidence("web_search", ok=False),
            _evidence("web_fetch", ok=True),
        ),
        task_artifacts=(
            TaskArtifact(
                kind="web_source",
                source_tool="web_fetch",
                content_preview="source",
                ok=True,
                metadata={"sources": [{"url": "https://example.com", "content_chars": 1000}]},
            ),
        ),
    )

    assert has_successful_fetched_web_source_artifact(result) is True
    assert has_only_optional_web_discovery_failures(result) is True


def test_tool_failure_policy_requires_successful_workspace_discovery_before_ignoring_batch():
    without_discovery = ExecutionResult(
        content="done",
        tool_evidence=(_evidence("batch", ok=False),),
    )
    with_discovery = ExecutionResult(
        content="done",
        tool_evidence=(
            _evidence("list_dir", ok=True),
            _evidence("batch", ok=False),
        ),
    )

    assert has_only_optional_workspace_discovery_failures(without_discovery) is False
    assert has_only_optional_workspace_discovery_failures(with_discovery) is True


def test_tool_failure_policy_allows_history_failures_only_after_history_success():
    without_history_success = ExecutionResult(
        content="done",
        tool_evidence=(_evidence("web_search", ok=False),),
    )
    with_history_success = ExecutionResult(
        content="done",
        tool_evidence=(
            _evidence("search_history", ok=True),
            _evidence("search_history", ok=False),
        ),
    )

    assert has_only_optional_history_retrieval_failures(without_history_success) is False
    assert has_only_optional_history_retrieval_failures(with_history_success) is True
