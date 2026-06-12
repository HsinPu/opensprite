import asyncio
import json
from dataclasses import replace

import opensprite.agent.execution as execution_module
from opensprite.agent.completion_gate import CompletionGateService
from opensprite.agent.execution import ExecutionEngine
from opensprite.agent.execution_support.artifacts import TaskArtifact
from opensprite.agent.execution_support.events import (
    LLM_COMPACTION_EMPTY_REASON,
    LLM_STEP_COMPLETED_STATUS,
    LLM_STEP_ERROR_STATUS,
    MAX_TOOL_ITERATIONS_STOP_REASON,
)
from opensprite.agent.execution_support.prompt_logging import PromptLoggingService
from opensprite.tools.evidence import SOURCE_MATERIAL_INSUFFICIENT_REASON
from tests.agent.task_contract_test_helpers import TaskContractService
from opensprite.agent.task.contract import TaskIntentService
from opensprite.config.schema import Config, ToolsConfig, WebSearchToolConfig
from opensprite.llms.base import ChatMessage, LLMResponse, ToolCall
from opensprite.tools.base import Tool
from opensprite.tools.credential_store import CredentialStoreTool
from opensprite.tools.evidence import ToolEvidence
from opensprite.tools.image import AnalyzeImageTool
from opensprite.tools.registry import ToolRegistry
from opensprite.tools.result_status import tool_error_result
from opensprite.tools.web_fetch import WebFetchTool
from opensprite.tools.web_search import WebSearchTool, _format_results


def test_execution_extracts_delegate_task_info_from_shared_result_labels():
    task_id, prompt_type = ExecutionEngine._extract_delegate_task_info(
        "Task ID: task_abc123\nSubagent: reviewer\n\nResult:\nlooks good"
    )

    assert task_id == "task_abc123"
    assert prompt_type == "reviewer"


class DummyTool(Tool):
    @property
    def name(self) -> str:
        return "demo_tool"

    @property
    def description(self) -> str:
        return "Demo tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"value": {"type": "string"}}}

    async def _execute(self, value: str, **kwargs) -> str:
        return f"tool:{value}"


class EvidenceTool(Tool):
    @property
    def name(self) -> str:
        return "evidence_tool"

    @property
    def description(self) -> str:
        return "Tool with custom evidence"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"resource": {"type": "string"}}}

    async def _execute(self, resource: str, **kwargs) -> str:
        return f"read:{resource}"

    def build_evidence(self, params, result: str, *, ok: bool) -> ToolEvidence:
        return ToolEvidence(
            name=self.name,
            args=dict(params or {}),
            ok=ok,
            resource_ids=(f"custom:{params.get('resource')}",),
            result_preview=str(result or "")[:240],
        )


class FailingTool(Tool):
    def __init__(self):
        self.calls = 0

    @property
    def name(self) -> str:
        return "failing_tool"

    @property
    def description(self) -> str:
        return "Always fails"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"value": {"type": "string"}}}

    async def _execute(self, value: str, **kwargs) -> str:
        self.calls += 1
        return tool_error_result(
            "still broken",
            error_type="ToolExecutionError",
            category="test_failure",
            metadata={"tool_name": self.name},
        )


class TaskUpdateLikeTool(Tool):
    def __init__(self):
        self.calls = 0

    @property
    def name(self) -> str:
        return "task_update"

    @property
    def description(self) -> str:
        return "Task update"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"action": {"type": "string"}}}

    async def _execute(self, action: str, **kwargs) -> str:
        self.calls += 1
        return "updated"


class TraceableWebResearchTool(Tool):
    @property
    def name(self) -> str:
        return "web_research"

    @property
    def description(self) -> str:
        return "Traceable web research"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"query": {"type": "string"}}}

    async def _execute(self, query: str, **kwargs) -> str:
        return json.dumps({
            "type": "web_research",
            "query": query,
            "source_count": 2,
            "fetched_count": 2,
            "sources": [
                {
                    "tool_name": "web_fetch",
                    "title": "Fresh source",
                    "url": "https://example.com/fresh",
                    "snippet": "fresh source text",
                    "content_chars": 1200,
                },
                {
                    "tool_name": "web_fetch",
                    "title": "Second source",
                    "url": "https://example.com/second",
                    "snippet": "second source text",
                    "content_chars": 1000,
                },
            ],
            "coverage": {"target_met": True, "fetched_count": 2, "failed_count": 0},
        })


class RepeatingReadFileTool(Tool):
    def __init__(self):
        self.calls = 0

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a file"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"path": {"type": "string"}}}

    async def _execute(self, path: str, **kwargs) -> str:
        self.calls += 1
        return "same file content"


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        return self.responses.pop(0)

    def get_default_model(self) -> str:
        return "fake-model"


class ContextKwargsProvider(FakeProvider):
    def context_request_kwargs(self, *, output_token_reserve: int):
        return {"max_tokens": output_token_reserve}

    async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools, "max_tokens": max_tokens})
        return self.responses.pop(0)


class SlowProvider:
    def __init__(self, delay: float = 1.0):
        self.delay = delay
        self.calls = []

    async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        await asyncio.sleep(self.delay)
        return LLMResponse(content="too late", model="fake-model")

    def get_default_model(self) -> str:
        return "fake-model"


class FakeMediaRouter:
    async def analyze_image(self, *, instruction, images, image_index=0):
        return f"image analysis:{image_index}:{instruction}"


class FakeWebFetcher:
    def __init__(
        self,
        max_chars=50000,
        max_response_size=5242880,
        timeout=30,
        prefer_trafilatura=True,
        firecrawl_api_key=None,
        **kwargs,
    ):
        self.max_chars = max_chars
        self.max_response_size = max_response_size
        self.timeout = timeout
        self.prefer_trafilatura = prefer_trafilatura
        self.firecrawl_api_key = firecrawl_api_key

    def fetch(self, url: str):
        return {
            "url": url,
            "finalUrl": f"{url}?ref=1",
            "status": 200,
            "title": "SQLite FTS5",
            "extractor": "trafilatura",
            "contentType": "text/html",
            "truncated": False,
            "text": " ".join(["SQLite FTS5 supports full text search over local tables."] * 20),
        }


class StreamingProvider:
    def __init__(self, content):
        self.content = content

    async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
        callback = kwargs.get("response_delta_callback")
        if callback is not None:
            await callback(self.content[:5])
            await callback(self.content[5:])
        return LLMResponse(content=self.content, model="fake-model")


class BlockingOverrideProvider:
    def __init__(self):
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            await self.release.wait()
            return LLMResponse(content="override-first", model="override-model")
        return LLMResponse(content="override-extra", model="override-model")

    def get_default_model(self) -> str:
        return "override-model"


class ToolInputStreamingProvider:
    async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
        callback = kwargs.get("tool_input_delta_callback")
        if callback is not None:
            await callback("call-1", "demo_tool", '{"value"', 1)
            await callback("call-1", "demo_tool", ':"abc"}', 2)
        return LLMResponse(content="done", model="fake-model")


class CredentialToolProvider:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        callback = kwargs.get("tool_input_delta_callback")
        if len(self.calls) == 1:
            if callback is not None:
                await callback("tc1", "credential_store", '{"secret":"router-secret"}', 1)
            return LLMResponse(
                content="saving",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="credential_store",
                        arguments={"action": "add", "provider": "openrouter", "secret": "router-secret"},
                    )
                ],
            )
        return LLMResponse(content="done", model="fake-model")


class ReasoningStreamingProvider:
    async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
        callback = kwargs.get("reasoning_delta_callback")
        if callback is not None:
            await callback("think ")
            await callback("more")
        return LLMResponse(content="done", model="fake-model")


class OverflowThenSuccessProvider:
    def __init__(
        self,
        final_response: str = "done",
        error_message: str = "This model's maximum context length was exceeded",
    ):
        self.final_response = final_response
        self.error_message = error_message
        self.calls = []

    async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
        self.calls.append({
            "messages": [ChatMessage(role=m.role, content=m.content, tool_call_id=m.tool_call_id, tool_calls=m.tool_calls) for m in messages],
            "tools": tools,
        })
        if len(self.calls) == 1:
            raise RuntimeError(self.error_message)
        return LLMResponse(content=self.final_response, model="fake-model")


class RetryableThenSuccessProvider:
    def __init__(self, final_response: str = "retry ok"):
        self.final_response = final_response
        self.calls = []
        self.recover_calls = 0

    async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
        self.calls.append({"messages": list(messages), "tools": tools})
        if len(self.calls) == 1:
            error = RuntimeError("rate limited")
            error.status_code = 429
            error.headers = {"retry-after-ms": "0"}
            raise error
        return LLMResponse(content=self.final_response, model="fake-model")

    def recover_after_error(self, error: BaseException) -> bool:
        self.recover_calls += 1
        return True


class LlmCompactionProvider:
    def __init__(self, compaction_response: str, final_response: str = "done"):
        self.compaction_response = compaction_response
        self.final_response = final_response
        self.calls = []

    async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
        self.calls.append({
            "messages": [ChatMessage(role=m.role, content=m.content, tool_call_id=m.tool_call_id, tool_calls=m.tool_calls) for m in messages],
            "tools": tools,
            "max_tokens": max_tokens,
        })
        if len(self.calls) == 1:
            return LLMResponse(content=self.compaction_response, model="fake-model")
        return LLMResponse(content=self.final_response, model="fake-model")


async def _save_message_collector(calls, session_id, role, content, tool_name=None, metadata=None):
    calls.append((session_id, role, content, tool_name, dict(metadata or {})))


def _make_engine(provider, registry, save_calls, tools_config=None, **engine_kwargs):
    chat_kwargs = Config.packaged_execution_engine_chat_kwargs()
    chat_kwargs.update(engine_kwargs)
    return ExecutionEngine(
        provider=provider,
        tools=registry,
        tools_config=tools_config or ToolsConfig(max_tool_iterations=3),
        empty_response_fallback="EMPTY",
        repeated_invalid_tool_call_fallback="REPEATED INVALID TOOL CALL\n{result}",
        save_message=lambda session_id, role, content, tool_name=None, metadata=None: _save_message_collector(
            save_calls, session_id, role, content, tool_name, metadata
        ),
        format_log_preview=lambda text, max_chars=200: str(text)[:max_chars],
        summarize_messages=lambda messages, tail=4: f"count={len(messages)}",
        sanitize_response_content=lambda text: text.strip(),
        **chat_kwargs,
    )


def test_execution_engine_force_final_after_web_sources_uses_shared_policy():
    artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_research",
        ok=True,
        metadata={
            "sources": [{"url": "https://example.com/a"}],
            "coverage": {"target_met": True},
        },
    )

    assert ExecutionEngine._should_force_final_after_web_sources([artifact], []) is True

    non_research_artifact = TaskArtifact(
        kind="web_source",
        source_tool="web_search",
        ok=True,
        metadata={"sources": [{"url": "https://example.com/a"}]},
    )
    evidence = [
        ToolEvidence(name="web_search", args={}, ok=True, metadata={"sources": [{"url": "https://example.com/a"}]}),
        ToolEvidence(name="web_fetch", args={}, ok=True, metadata={"sources": [{"url": "https://example.com/b"}]}),
    ]

    assert ExecutionEngine._should_force_final_after_web_sources([non_research_artifact], evidence) is True


def test_execution_engine_passes_provider_context_request_kwargs():
    provider = ContextKwargsProvider([LLMResponse(content="done", model="fake-model")])
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_output_reserve_tokens=12345,
    )

    result = asyncio.run(engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False))

    assert result.content == "done"
    assert provider.calls[0]["max_tokens"] == 12345


def test_execution_engine_runs_tool_loop_and_persists_tool_result():
    registry = ToolRegistry()
    registry.register(DummyTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="demo_tool", arguments={"value": "abc"})],
            ),
            LLMResponse(content="done", model="fake-model"),
        ]
    )
    save_calls = []
    engine = _make_engine(provider, registry, save_calls)
    messages = [ChatMessage(role="user", content="hi")]

    result = asyncio.run(
        engine.execute_messages("chat-1", messages, allow_tools=True, tool_result_session_id="chat-1")
    )

    assert result.content == "done"
    assert result.executed_tool_calls == 1
    assert result.used_configure_skill is False
    assert save_calls == [
        ("chat-1", "tool", "tool:abc", "demo_tool", {"tool_args": {"value": "abc"}})
    ]
    assert [message.role for message in messages] == ["user", "assistant", "tool"]
    assert messages[1].tool_calls[0]["function"]["name"] == "demo_tool"


def test_execution_engine_provider_override_does_not_mutate_concurrent_runs():
    async def run_case():
        registry = ToolRegistry()
        base_provider = FakeProvider([LLMResponse(content="base", model="base-model")])
        override_provider = BlockingOverrideProvider()
        engine = _make_engine(base_provider, registry, [])

        first = asyncio.create_task(
            engine.execute_messages(
                "chat-override",
                [ChatMessage(role="user", content="override")],
                allow_tools=False,
                provider_override=override_provider,
            )
        )
        await asyncio.wait_for(override_provider.entered.wait(), timeout=1)

        second = await engine.execute_messages(
            "chat-base",
            [ChatMessage(role="user", content="base")],
            allow_tools=False,
        )
        override_provider.release.set()
        first_result = await first
        return first_result, second, base_provider, override_provider, engine

    first_result, second, base_provider, override_provider, engine = asyncio.run(run_case())

    assert first_result.content == "override-first"
    assert second.content == "base"
    assert len(base_provider.calls) == 1
    assert override_provider.calls == 1
    assert engine.provider is base_provider


def test_execution_engine_uses_tool_defined_evidence():
    registry = ToolRegistry()
    registry.register(EvidenceTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need evidence",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="evidence_tool", arguments={"resource": "a"})],
            ),
            LLMResponse(content="done", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, registry, [])

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="read resource")],
            allow_tools=True,
        )
    )

    assert result.content == "done"
    assert result.tool_evidence[0].name == "evidence_tool"
    assert result.tool_evidence[0].resource_ids == ("custom:a",)


def test_execution_engine_builds_task_artifacts_from_media_evidence():
    registry = ToolRegistry()
    registry.register(
        AnalyzeImageTool(
            media_router=FakeMediaRouter(),
            get_current_images=lambda: ["data:image/png;base64,abc"],
        )
    )
    provider = FakeProvider(
        [
            LLMResponse(
                content="need image",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="analyze_image",
                        arguments={"instruction": "read prompt", "image_index": 0},
                    )
                ],
            ),
            LLMResponse(content="done", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, registry, [])

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="read the image")],
            allow_tools=True,
        )
    )

    assert result.content == "done"
    assert result.tool_evidence[0].resource_ids == ("image_index:0",)
    assert result.task_artifacts[0].kind == "image_analysis"
    assert result.task_artifacts[0].source_tool == "analyze_image"
    assert result.task_artifacts[0].resource_ids == ("image_index:0",)
    assert "image analysis:0" in result.task_artifacts[0].content_preview


def test_execution_engine_builds_traceable_web_search_artifact(monkeypatch):
    registry = ToolRegistry()
    tool = WebSearchTool(config=WebSearchToolConfig(provider="duckduckgo", max_results=3))

    async def fake_search(query, n, freshness):
        return _format_results(
            query,
            [
                {
                    "title": "Reddit API docs",
                    "url": "https://www.reddit.com/dev/api/",
                    "content": "Official Reddit API documentation for listings and search.",
                },
                {
                    "title": "Reddit API wiki",
                    "url": "https://www.reddit.com/wiki/api/",
                    "content": "Reddit API wiki with additional integration notes.",
                }
            ],
            n,
            provider="duckduckgo",
        )

    monkeypatch.setattr(tool, "_search_duckduckgo", fake_search)
    registry.register(tool)
    provider = FakeProvider(
        [
            LLMResponse(
                content="need web",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="web_search",
                        arguments={"query": "reddit api search"},
                    )
                ],
            ),
            LLMResponse(
                content=(
                    "I found Reddit API docs at reddit.com. They are the best first source for authenticated "
                    "search and listing behavior before adding any third-party historical source."
                ),
                model="fake-model",
            ),
        ]
    )
    engine = _make_engine(provider, registry, [])

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="Please find Reddit API search sources")],
            allow_tools=True,
        )
    )

    assert result.task_artifacts[0].kind == "web_source"
    assert result.task_artifacts[0].metadata["source_count"] == 2
    source = result.task_artifacts[0].metadata["sources"][0]
    assert source["url"] == "https://www.reddit.com/dev/api/"
    assert source["title"] == "Reddit API docs"
    assert source["snippet"] == "Official Reddit API documentation for listings and search."

    intent = TaskIntentService().classify("Please find Reddit API search sources")
    task_contract = TaskContractService.build(
        task_intent=intent,
        current_message="Please find Reddit API search sources",
    )
    completion = CompletionGateService().evaluate(
        task_intent=intent,
        response_text=result.content,
        execution_result=replace(result, task_contract=task_contract),
    )
    assert completion.status == "incomplete"
    assert completion.reason == SOURCE_MATERIAL_INSUFFICIENT_REASON


def test_execution_engine_builds_traceable_web_fetch_artifact(monkeypatch):
    monkeypatch.setattr(
        "opensprite.tools.web_fetch.WebFetcher",
        lambda *args, **kwargs: FakeWebFetcher(**kwargs),
    )
    registry = ToolRegistry()
    registry.register(WebFetchTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need fetch",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="web_fetch",
                        arguments={"url": "https://sqlite.org/fts5.html"},
                    )
                ],
            ),
            LLMResponse(
                content=(
                    "The sqlite.org FTS5 page explains that SQLite FTS5 supports full text search over local "
                    "tables, so it is the relevant source for local indexing behavior."
                ),
                model="fake-model",
            ),
        ]
    )
    engine = _make_engine(provider, registry, [])

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="Please fetch https://sqlite.org/fts5.html and summarize source")],
            allow_tools=True,
        )
    )

    artifact = result.task_artifacts[0]
    assert artifact.kind == "web_source"
    source = artifact.metadata["sources"][0]
    assert source["url"] == "https://sqlite.org/fts5.html?ref=1"
    assert source["title"] == "SQLite FTS5"
    assert "full text search" in source["snippet"]


def test_execution_engine_records_evidence_for_invalid_media_tool_args():
    registry = ToolRegistry()
    registry.register(
        AnalyzeImageTool(
            media_router=object(),
            get_current_images=lambda: ["data:image/png;base64,abc"],
        )
    )
    provider = FakeProvider(
        [
            LLMResponse(
                content="try image",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="analyze_image",
                        arguments={"instruction": "read", "image_index": "bad"},
                    )
                ],
            ),
            LLMResponse(content="done", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, registry, [])

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="read the image")],
            allow_tools=True,
        )
    )

    assert result.content == "done"
    assert result.had_tool_error is True
    assert result.tool_evidence[0].name == "analyze_image"
    assert result.tool_evidence[0].ok is False
    assert result.tool_evidence[0].resource_ids == ("image_index:0",)


def test_execution_engine_blocks_repeated_identical_tool_failures():
    tool = FailingTool()
    registry = ToolRegistry()
    registry.register(tool)
    provider = FakeProvider(
        [
            LLMResponse(
                content="try failing",
                model="fake-model",
                tool_calls=[ToolCall(id=f"tc{i}", name="failing_tool", arguments={"value": "abc"})],
            )
            for i in range(4)
        ]
        + [LLMResponse(content="done", model="fake-model")]
    )
    engine = _make_engine(provider, registry, [], tools_config=ToolsConfig(max_tool_iterations=5))
    messages = [ChatMessage(role="user", content="hi")]

    result = asyncio.run(engine.execute_messages("chat-1", messages, allow_tools=True))

    assert result.content == "done"
    assert result.executed_tool_calls == 3
    assert result.had_tool_error is True
    assert tool.calls == 3
    blocked_payload = json.loads(messages[-1].content)
    assert blocked_payload["error_type"] == "ToolGuardrailError"
    assert blocked_payload["category"] == "tool_guardrail"
    assert blocked_payload["guardrail"]["code"] == "repeated_failure_block"
    assert "Blocked failing_tool" in blocked_payload["error"]


def test_execution_engine_records_unavailable_tool_call_as_tool_error():
    tool = TaskUpdateLikeTool()
    registry = ToolRegistry()
    registry.register(RepeatingReadFileTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="try hidden tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="task_update", arguments={"action": "show"})],
            ),
            LLMResponse(content="direct answer", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, registry, [], tools_config=ToolsConfig(max_tool_iterations=3))

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="plan only")], allow_tools=True)
    )

    assert result.content == "direct answer"
    assert result.had_tool_error is True
    assert result.executed_tool_calls == 1
    assert tool.calls == 0
    assert "not available in this turn" in provider.calls[1]["messages"][-1].content
    assert result.tool_evidence[0].ok is False


def test_execution_engine_forces_final_after_complete_web_research_sources():
    registry = ToolRegistry()
    registry.register(TraceableWebResearchTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="checking",
                model="fake-model",
                tool_calls=[ToolCall(id="tc-web", name="web_research", arguments={"query": "fresh news"})],
            ),
            LLMResponse(content="final with sources", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, registry, [], tools_config=ToolsConfig(max_tool_iterations=5))
    messages = [ChatMessage(role="user", content="find fresh sources")]

    result = asyncio.run(engine.execute_messages("chat-1", messages, allow_tools=True))

    assert result.content == "final with sources"
    assert result.executed_tool_calls == 1
    assert len(provider.calls) == 2
    assert provider.calls[1]["tools"] is None
    assert messages[-1].role == "system"
    assert "Stop calling tools now" in messages[-1].content
    assert result.task_artifacts[0].kind == "web_source"


def test_tool_result_failure_detection_allows_partial_web_research_payload():
    payload = {
        "type": "web_research",
        "query": "Qwen latest model",
        "fetched_count": 2,
        "sources": [{"title": "Qwen", "url": "https://qwen.ai/research/"}],
        "failed_sources": [{"title": "Candidate", "reason": "Search failed for exact query"}],
        "coverage": {"target_met": True, "fetched_count": 2, "failed_count": 1},
    }

    assert ExecutionEngine._tool_result_looks_like_failure(json.dumps(payload)) is False


def test_tool_result_failure_detection_honors_structured_error_payload():
    payload = {"type": "web_search", "ok": False, "error": "Search failed for: Qwen"}

    assert ExecutionEngine._tool_result_looks_like_failure(json.dumps(payload)) is True


def test_tool_result_repeated_error_key_requires_structured_payload():
    assert ExecutionEngine._classify_tool_result("Search failed for: Qwen") is None

    result = tool_error_result(
        "Search failed for: Qwen",
        error_type="ToolExecutionError",
        category="web_search_error",
        repeated_error_key="web_search:Qwen",
    )

    assert ExecutionEngine._classify_tool_result(result) == "web_search:Qwen"


def test_tool_result_failure_detection_allows_successful_batch_payload():
    result = json.dumps(
        {
            "type": "batch",
            "ok": True,
            "summary": "Batch completed: 3 call(s), 0 failed.",
            "total": 3,
            "failed": 0,
            "results": [],
        }
    )

    assert ExecutionEngine._tool_result_looks_like_failure(result) is False


def test_tool_result_failure_detection_flags_failed_batch_payload():
    result = json.dumps(
        {
            "type": "batch",
            "ok": False,
            "summary": "Batch completed: 3 call(s), 1 failed.",
            "total": 3,
            "failed": 1,
            "error": "Batch completed: 3 call(s), 1 failed.",
            "error_type": "ToolFailure",
            "category": "batch_failure",
            "results": [],
        }
    )

    assert ExecutionEngine._tool_result_looks_like_failure(result) is True


def test_execution_engine_warns_then_blocks_repeated_read_only_results():
    tool = RepeatingReadFileTool()
    registry = ToolRegistry()
    registry.register(tool)
    provider = FakeProvider(
        [
            LLMResponse(
                content="read again",
                model="fake-model",
                tool_calls=[ToolCall(id=f"tc{i}", name="read_file", arguments={"path": "README.md"})],
            )
            for i in range(4)
        ]
        + [LLMResponse(content="done", model="fake-model")]
    )
    engine = _make_engine(provider, registry, [], tools_config=ToolsConfig(max_tool_iterations=5))
    messages = [ChatMessage(role="user", content="hi")]

    result = asyncio.run(engine.execute_messages("chat-1", messages, allow_tools=True))

    assert result.content == "done"
    assert result.executed_tool_calls == 3
    assert tool.calls == 3
    tool_messages = [message.content for message in messages if message.role == "tool"]
    assert any("same_result_warning" in content for content in tool_messages)
    blocked_payload = json.loads(tool_messages[-1])
    assert blocked_payload["error_type"] == "ToolGuardrailError"
    assert blocked_payload["category"] == "tool_guardrail"
    assert blocked_payload["guardrail"]["code"] == "same_result_block"
    assert "Blocked read_file" in blocked_payload["error"]


def test_execution_engine_records_llm_step_usage_metadata():
    provider = FakeProvider([
        LLMResponse(
            content="done",
            model="fake-model",
            usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            finish_reason="stop",
        )
    ])
    engine = _make_engine(provider, ToolRegistry(), [])

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "done"
    assert len(result.llm_step_events) == 1
    step = result.llm_step_events[0]
    assert step.iteration == 1
    assert step.attempt == 1
    assert step.status == LLM_STEP_COMPLETED_STATUS
    assert step.provider == "FakeProvider"
    assert step.model == "fake-model"
    assert step.tools_enabled is False
    assert step.tool_count == 0
    assert step.output_tokens == 7
    assert step.total_tokens == 18
    assert step.finish_reason == "stop"
    assert step.estimated_input_tokens >= 1


def test_execution_engine_logs_llm_request_and_response_metadata(monkeypatch):
    provider = FakeProvider([
        LLMResponse(
            content="done",
            model="fake-model",
            usage={"completion_tokens": 7, "total_tokens": 18},
            finish_reason="stop",
            reasoning_details=[{"type": "reasoning.text", "text": "summary"}],
        )
    ])
    engine = _make_engine(provider, ToolRegistry(), [])
    messages = []
    monkeypatch.setattr(execution_module.logger, "info", lambda message, *args, **kwargs: messages.append(str(message)))

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "done"
    rendered = "\n".join(messages)
    assert "llm.request.attempt" in rendered
    assert "provider=FakeProvider" in rendered
    assert "model=fake-model" in rendered
    assert "tools=0" in rendered
    assert "estimated_tokens=" in rendered
    assert "llm.response" in rendered
    assert "finish_reason=stop" in rendered
    assert "output_tokens=7" in rendered
    assert "total_tokens=18" in rendered
    assert "reasoning_details=1" in rendered


def test_execution_engine_redacts_raw_hidden_block_logs(monkeypatch):
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz"
    provider = FakeProvider([
        LLMResponse(
            content=f"<system-reminder>OPENAI_API_KEY=\"{secret}\"</system-reminder>",
            model="fake-model",
        ),
        LLMResponse(content="done", model="fake-model"),
    ])
    engine = _make_engine(provider, ToolRegistry(), [])
    engine.format_log_preview = PromptLoggingService.format_log_preview
    engine.sanitize_response_content = lambda text: "" if "<system-reminder>" in text else text.strip()
    warnings = []
    monkeypatch.setattr(execution_module.logger, "warning", lambda message, *args, **kwargs: warnings.append(str(message)))

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "done"
    rendered = "\n".join(warnings)
    assert "llm.raw-hidden-blocks" in rendered
    assert secret not in rendered
    assert "OPENAI_API_KEY=\"sk-pro...wxyz\"" in rendered


def test_execution_engine_retries_transient_provider_errors_with_metadata():
    provider = RetryableThenSuccessProvider()
    engine = _make_engine(provider, ToolRegistry(), [])
    statuses = []

    async def on_status(message):
        statuses.append(message)

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="hi")],
            allow_tools=False,
            on_llm_status=on_status,
        )
    )

    assert result.content == "retry ok"
    assert len(provider.calls) == 2
    assert provider.recover_calls == 1
    assert [event.status for event in result.llm_step_events] == [
        LLM_STEP_ERROR_STATUS,
        LLM_STEP_COMPLETED_STATUS,
    ]
    assert result.llm_step_events[0].retryable is True
    assert result.llm_step_events[0].retry_after_ms == 0
    assert result.llm_step_events[0].next_retry_at is not None
    assert result.llm_step_events[0].provider == "RetryableThenSuccessProvider"
    assert result.llm_step_events[1].provider == "RetryableThenSuccessProvider"
    assert statuses == [
        {
            "message": ExecutionEngine.PROVIDER_RETRY_STATUS_MESSAGE,
            "status": "retry",
            "trigger": "provider_retry",
        }
    ]


def test_execution_engine_retries_transient_transport_errors_without_status_code(monkeypatch):
    monkeypatch.setattr("opensprite.llms.retry.random.random", lambda: 0.0)

    class TransportThenSuccessProvider:
        def __init__(self):
            self.calls = []
            self.recover_calls = 0

        async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
            self.calls.append({"messages": list(messages), "tools": tools})
            if len(self.calls) == 1:
                raise RuntimeError("connection reset by peer")
            return LLMResponse(content="transport ok", model="fake-model")

        def recover_after_error(self, error: BaseException) -> bool:
            self.recover_calls += 1
            return True

    provider = TransportThenSuccessProvider()
    engine = _make_engine(provider, ToolRegistry(), [])

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="hi")],
            allow_tools=False,
        )
    )

    assert result.content == "transport ok"
    assert len(provider.calls) == 2
    assert provider.recover_calls == 1
    assert result.llm_step_events[0].retryable is True
    assert result.llm_step_events[0].retry_after_ms == 1000


def test_execution_engine_projects_final_response_as_deltas():
    provider = FakeProvider([LLMResponse(content="hello streaming world", model="fake-model")])
    save_calls = []
    engine = _make_engine(provider, ToolRegistry(), save_calls)
    messages = [ChatMessage(role="user", content="hi")]
    deltas = []

    async def on_delta(part_id, delta, state, sequence):
        deltas.append((part_id, delta, state, sequence))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=False,
            on_response_delta=on_delta,
        )
    )

    assert result.content == "hello streaming world"
    assert "".join(item[1] for item in deltas) == "hello streaming world"
    assert deltas == [("assistant:chat-1:1", "hello streaming world", "completed", 1)]


def test_execution_engine_forwards_tool_input_deltas():
    provider = ToolInputStreamingProvider()
    engine = _make_engine(provider, ToolRegistry(), [])
    deltas = []

    async def on_tool_input(call_id, tool_name, delta, sequence):
        deltas.append((call_id, tool_name, delta, sequence))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="hi")],
            allow_tools=False,
            on_tool_input_delta=on_tool_input,
        )
    )

    assert result.content == "done"
    assert deltas == [("call-1", "demo_tool", '{"value"', 1), ("call-1", "demo_tool", ':"abc"}', 2)]


def test_execution_engine_redacts_credential_tool_secret_from_trace_and_followup_context(tmp_path):
    registry = ToolRegistry()
    registry.register(CredentialStoreTool(app_home=tmp_path))
    provider = CredentialToolProvider()
    save_calls = []
    before_calls = []
    after_calls = []
    input_deltas = []
    engine = _make_engine(provider, registry, save_calls)

    async def on_before(name, params, call_id, iteration):
        before_calls.append((name, params, call_id, iteration))

    async def on_after(name, params, result, call_id, iteration, *_):
        after_calls.append((name, params, result, call_id, iteration))

    async def on_tool_input(call_id, tool_name, delta, sequence):
        input_deltas.append((call_id, tool_name, delta, sequence))

    messages = [ChatMessage(role="user", content="save my OpenRouter key")]

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=True,
            tool_result_session_id="chat-1",
            on_tool_before_execute=on_before,
            on_tool_after_execute=on_after,
            on_tool_input_delta=on_tool_input,
        )
    )

    assert result.content == "done"
    assert before_calls == [
        (
            "credential_store",
            {"action": "add", "provider": "openrouter", "secret": "***redacted***"},
            "tc1",
            1,
        )
    ]
    assert after_calls[0][0:2] == (
        "credential_store",
        {"action": "add", "provider": "openrouter", "secret": "***redacted***"},
    )
    assert "router-secret" not in after_calls[0][2]
    assert input_deltas == [("tc1", "credential_store", "***redacted***", 1)]
    assert save_calls[0][4] == {
        "tool_args": {"action": "add", "provider": "openrouter", "secret": "***redacted***"}
    }
    assert "router-secret" not in save_calls[0][2]

    followup_messages = provider.calls[1]["messages"]
    rendered_followup = "\n".join(
        [message.content or "" for message in followup_messages]
        + [str(message.tool_calls or "") for message in followup_messages]
    )
    assert "router-secret" not in rendered_followup
    assert "***redacted***" in rendered_followup


def test_execution_engine_forwards_reasoning_deltas():
    provider = ReasoningStreamingProvider()
    engine = _make_engine(provider, ToolRegistry(), [])
    deltas = []

    async def on_reasoning(delta):
        deltas.append(delta)

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="hi")],
            allow_tools=False,
            on_reasoning_delta=on_reasoning,
        )
    )

    assert result.content == "done"
    assert deltas == ["think ", "more"]


def test_execution_engine_marks_provider_streamed_response_completed():
    provider = StreamingProvider("hello streaming world")
    save_calls = []
    engine = _make_engine(provider, ToolRegistry(), save_calls)
    messages = [ChatMessage(role="user", content="hi")]
    deltas = []

    async def on_delta(part_id, delta, state, sequence):
        deltas.append((part_id, delta, state, sequence))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=False,
            on_response_delta=on_delta,
        )
    )

    assert result.content == "hello streaming world"
    assert deltas == [
        ("assistant:chat-1:1", "hello", "running", 1),
        ("assistant:chat-1:1", " streaming world", "running", 2),
        ("assistant:chat-1:1", "", "completed", 3),
    ]


def test_execution_engine_calls_on_tool_before_execute_before_tool_run():
    registry = ToolRegistry()
    registry.register(DummyTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="demo_tool", arguments={"value": "abc"})],
            ),
            LLMResponse(content="done", model="fake-model"),
        ]
    )
    save_calls = []
    engine = _make_engine(provider, registry, save_calls)
    messages = [ChatMessage(role="user", content="hi")]
    order = []

    async def before(name, args):
        order.append(("before", name, args))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=True,
            tool_result_session_id="chat-1",
            on_tool_before_execute=before,
        )
    )

    assert result.content == "done"
    assert order == [("before", "demo_tool", {"value": "abc"})]


def test_execution_engine_uses_empty_fallback_for_blank_visible_response():
    provider = FakeProvider(
        [
            LLMResponse(content="   ", model="fake-model"),
            LLMResponse(content="retry ok", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "retry ok"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.EMPTY_RESPONSE_RETRY_MESSAGE


def test_execution_engine_times_out_slow_provider_calls():
    provider = SlowProvider(delay=0.1)
    engine = _make_engine(provider, ToolRegistry(), [], llm_request_timeout_seconds=0.01)

    try:
        asyncio.run(engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False))
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected provider call timeout")

    assert len(provider.calls) == 2


def test_execution_engine_uses_sanitized_empty_retry_message_for_hidden_only_content():
    provider = FakeProvider(
        [
            LLMResponse(content="<think>secret</think>", model="fake-model"),
            LLMResponse(content="retry ok", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])
    engine.sanitize_response_content = lambda text: "" if "<think>" in text else text.strip()

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "retry ok"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE


def test_execution_engine_retries_when_minimax_text_tool_call_is_visible_content():
    provider = FakeProvider(
        [
            LLMResponse(
                content=(
                    "<minimax:tool_call>\n"
                    "<invoke name=\"web_fetch\">\n"
                    "<parameter name=\"url\">https://example.com</parameter>\n"
                    "</invoke>\n"
                    "</minimax:tool_call>"
                ),
                model="fake-model",
            ),
            LLMResponse(content="plain final answer", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])
    engine.sanitize_response_content = PromptLoggingService.sanitize_response_content

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "plain final answer"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE


def test_execution_engine_marks_ignored_structured_tool_call_as_internal_only():
    provider = FakeProvider(
        [
            LLMResponse(
                content="",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="web_search", arguments={"query": "news"})],
            ),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "EMPTY"
    assert result.assistant_internal_only_response is True
    assert result.executed_tool_calls == 0


def test_execution_engine_retries_when_bracket_text_tool_call_is_visible_content():
    provider = FakeProvider(
        [
            LLMResponse(
                content=(
                    "[TOOL_CALL]\n"
                    "{tool => \"web_fetch\", args => { --url \"https://example.com\" }}\n"
                    "[/TOOL_CALL]"
                ),
                model="fake-model",
            ),
            LLMResponse(content="plain final answer", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])
    engine.sanitize_response_content = PromptLoggingService.sanitize_response_content

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "plain final answer"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE


def test_execution_engine_retries_when_generic_xml_text_tool_call_is_visible_content():
    provider = FakeProvider(
        [
            LLMResponse(
                content=(
                    '<tool_call name="web_research">\n'
                    '{"query": "台積電 今日股價"}\n'
                    "</tool_call>"
                ),
                model="fake-model",
            ),
            LLMResponse(content="plain final answer", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])
    engine.sanitize_response_content = PromptLoggingService.sanitize_response_content

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "plain final answer"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE


def test_execution_engine_retries_when_function_calls_text_tool_call_is_visible_content():
    provider = FakeProvider(
        [
            LLMResponse(
                content=(
                    "<function_calls>\n"
                    '<invoke name="web_fetch">\n'
                    '<arg name="url">https://example.com</arg>\n'
                    "</invoke>\n"
                    "</function_calls>"
                ),
                model="fake-model",
            ),
            LLMResponse(content="plain final answer", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])
    engine.sanitize_response_content = PromptLoggingService.sanitize_response_content

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "plain final answer"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE


def test_execution_engine_retries_when_direct_tool_tag_text_call_is_visible_content():
    provider = FakeProvider(
        [
            LLMResponse(
                content=(
                    "<search_history>\n"
                    "<query>AMD stock price 2026-05-28 web research</query>\n"
                    "<limit>10</limit>\n"
                    "</search_history>"
                ),
                model="fake-model",
            ),
            LLMResponse(content="plain final answer", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])
    engine.sanitize_response_content = PromptLoggingService.sanitize_response_content

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "plain final answer"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE


def test_execution_engine_retries_when_dsml_text_tool_call_is_visible_content():
    provider = FakeProvider(
        [
            LLMResponse(
                content=(
                    "<｜｜DSML｜｜tool_calls>\n"
                    '<｜｜DSML｜｜invoke name="web_fetch">\n'
                    '<｜｜DSML｜｜parameter name="url" string="true">https://example.com</｜｜DSML｜｜parameter>\n'
                    "</｜｜DSML｜｜invoke>\n"
                    "<｜｜DSML｜｜/tool_calls>"
                ),
                model="fake-model",
            ),
            LLMResponse(content="plain final answer", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])
    engine.sanitize_response_content = PromptLoggingService.sanitize_response_content

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "plain final answer"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE


def test_execution_engine_retries_when_single_bar_dsml_text_tool_call_is_visible_content():
    provider = FakeProvider(
        [
            LLMResponse(
                content=(
                    "<｜DSML｜tool_calls>\n"
                    '<｜DSML｜invoke name="web_fetch">\n'
                    '<｜DSML｜parameter name="url" string="true">https://example.com</｜DSML｜parameter>\n'
                    "</｜DSML｜invoke>\n"
                    "<｜DSML｜/tool_calls>"
                ),
                model="fake-model",
            ),
            LLMResponse(content="plain final answer", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])
    engine.sanitize_response_content = PromptLoggingService.sanitize_response_content

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "plain final answer"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][-1].content == ExecutionEngine.SANITIZED_EMPTY_RESPONSE_RETRY_MESSAGE


def test_execution_engine_falls_back_after_second_blank_visible_response():
    provider = FakeProvider(
        [
            LLMResponse(content="   ", model="fake-model"),
            LLMResponse(content="", model="fake-model"),
        ]
    )
    engine = _make_engine(provider, ToolRegistry(), [])

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False)
    )

    assert result.content == "EMPTY"
    assert len(provider.calls) == 2


def test_execution_engine_returns_max_iteration_message_when_tool_loop_never_finishes():
    registry = ToolRegistry()
    registry.register(DummyTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="loop",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="demo_tool", arguments={"value": "abc"})],
            )
        ]
    )
    engine = _make_engine(provider, registry, [], tools_config=ToolsConfig(max_tool_iterations=1))

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=True)
    )

    assert "超過了最大迭代次數（1次）" in result.content
    assert "demo_tool: tool:abc" in result.content
    assert result.stop_reason == MAX_TOOL_ITERATIONS_STOP_REASON
    assert result.stop_metadata == {
        "schema_version": 1,
        "iteration_limit": 1,
        "executed_tool_calls": 1,
        "tool_result_count": 1,
    }
    assert len(result.llm_step_events) == 1
    assert result.llm_step_events[0].tool_calls == 1


def test_execution_user_tool_history_hides_raw_truncated_context():
    summary = ExecutionEngine._format_tool_history_for_user(
        [
            "web_fetch: [tool:web_fetch] Output truncated for context. "
            "Full result was persisted separately (1453 chars total).\n"
            "--- BEGIN HEAD ---\n{\"type\": \"web_fetch\", \"query\": \"https://example.test\"}",
            'web_search: {"type": "web_search", "ok": false, "summary": "Search failed for: agentic AI',
        ]
    )

    assert "web_fetch: 工具輸出過長" in summary
    assert "web_search: Search failed for: agentic AI" in summary
    assert "[tool:web_fetch]" not in summary
    assert "--- BEGIN HEAD ---" not in summary


def test_execution_engine_stops_when_cancel_checker_requests_stop():
    provider = FakeProvider([LLMResponse(content="done", model="fake-model")])
    engine = _make_engine(provider, ToolRegistry(), [])

    try:
        asyncio.run(
            engine.execute_messages(
                "chat-1",
                [ChatMessage(role="user", content="hi")],
                allow_tools=False,
                should_cancel=lambda: True,
            )
        )
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("CancelledError was not raised")


def test_execution_engine_records_aborted_tool_result_when_cancelled_after_tool_start():
    class CancellingTool(Tool):
        @property
        def name(self) -> str:
            return "cancel_tool"

        @property
        def description(self) -> str:
            return "Cancel after this tool runs"

        @property
        def parameters(self) -> dict:
            return {"type": "object", "properties": {}}

        async def _execute(self, **kwargs) -> str:
            cancel_requested["value"] = True
            return "finished"

    registry = ToolRegistry()
    registry.register(CancellingTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="cancel_tool", arguments={})],
            ),
        ]
    )
    save_calls = []
    engine = _make_engine(provider, registry, save_calls)
    cancel_requested = {"value": False}
    after_calls = []

    async def after(*args):
        after_calls.append(args)

    try:
        asyncio.run(
            engine.execute_messages(
                "chat-1",
                [ChatMessage(role="user", content="hi")],
                allow_tools=True,
                should_cancel=lambda: cancel_requested["value"],
                on_tool_after_execute=after,
            )
        )
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("CancelledError was not raised")

    assert len(after_calls) == 1
    assert after_calls[0][0] == "cancel_tool"
    assert after_calls[0][1] == {}
    aborted_payload = json.loads(after_calls[0][2])
    assert aborted_payload["error"] == "Tool execution aborted"
    assert aborted_payload["error_type"] == "ToolGuardrailError"
    assert aborted_payload["category"] == "tool_guardrail"
    assert after_calls[0][3:5] == ("tc1", 1)
    assert after_calls[0][-2:] == ("error", True)


def test_execution_engine_stops_after_repeated_missing_required_tool_errors():
    class MissingArgsTool(Tool):
        @property
        def name(self) -> str:
            return "write_file"

        @property
        def description(self) -> str:
            return "write_file"

        @property
        def parameters(self) -> dict:
            return {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            }

        async def _execute(self, **kwargs) -> str:
            raise AssertionError("_execute should not be called when validation fails")

    registry = ToolRegistry()
    registry.register(MissingArgsTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="write_file", arguments={})],
            ),
            LLMResponse(
                content="",
                model="fake-model",
                tool_calls=[ToolCall(id="tc2", name="write_file", arguments={})],
            ),
        ]
    )
    engine = _make_engine(provider, registry, [], tools_config=ToolsConfig(max_tool_iterations=10))

    result = asyncio.run(
        engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=True)
    )

    assert "REPEATED INVALID TOOL CALL" in result.content
    assert "Invalid arguments for write_file" in result.content
    assert len(provider.calls) == 2


def test_execution_engine_slims_tool_result_for_context_but_persists_full_result():
    class VerboseTool(Tool):
        @property
        def name(self) -> str:
            return "verbose_tool"

        @property
        def description(self) -> str:
            return "Verbose tool"

        @property
        def parameters(self) -> dict:
            return {"type": "object", "properties": {}}

        async def _execute(self, **kwargs) -> str:
            return "A" * 2000 + "TAIL"

    registry = ToolRegistry()
    registry.register(VerboseTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="verbose_tool", arguments={})],
            ),
            LLMResponse(content="done", model="fake-model"),
        ]
    )
    save_calls = []
    engine = _make_engine(provider, registry, save_calls)
    messages = [ChatMessage(role="user", content="hi")]

    result = asyncio.run(
        engine.execute_messages("chat-1", messages, allow_tools=True, tool_result_session_id="chat-1")
    )

    assert result.content == "done"
    assert save_calls == [
        ("chat-1", "tool", "A" * 2000 + "TAIL", "verbose_tool", {"tool_args": {}})
    ]
    assert messages[-1].role == "tool"
    assert "Output truncated for context" in messages[-1].content
    assert len(messages[-1].content) < len("A" * 2000 + "TAIL")
    assert messages[-1].content.endswith("TAIL")


def test_execution_engine_uses_configured_tool_result_context_limit():
    class VerboseTool(Tool):
        @property
        def name(self) -> str:
            return "verbose_tool"

        @property
        def description(self) -> str:
            return "Verbose tool"

        @property
        def parameters(self) -> dict:
            return {"type": "object", "properties": {}}

        async def _execute(self, **kwargs) -> str:
            return "A" * 2000 + "TAIL"

    registry = ToolRegistry()
    registry.register(VerboseTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="verbose_tool", arguments={})],
            ),
            LLMResponse(content="done", model="fake-model"),
        ]
    )
    engine = _make_engine(
        provider,
        registry,
        [],
        tools_config=ToolsConfig(max_tool_iterations=3, tool_result_max_chars=400),
    )
    messages = [ChatMessage(role="user", content="hi")]

    asyncio.run(engine.execute_messages("chat-1", messages, allow_tools=True))

    assert "Output truncated for context" in messages[-1].content
    assert len(messages[-1].content) < 700
    assert messages[-1].content.endswith("TAIL")


def test_exec_tool_result_slimming_keeps_timeout_and_stderr_highlights():
    result = tool_error_result(
        (
            "Command timed out after 60s. The command may be waiting for interactive input or may be stuck.\n"
            "Partial output before timeout:\n"
            + ("line\n" * 400)
            + "[stderr] npm ERR! missing script: build\n"
            + "final line\n"
        ),
        error_type="ToolExecutionError",
        category="timeout",
        metadata={"tool_name": "exec"},
    )

    summary = ExecutionEngine._summarize_tool_result_for_context("exec", result)

    assert "Output truncated for context" in summary
    assert "Timeout/Error summary:" in summary
    assert "timed out after 60s" in summary
    assert "stderr highlights:" in summary
    assert "missing script: build" in summary
    assert "output tail:" in summary
    assert "final line" in summary


def test_exec_tool_result_slimming_ignores_incidental_timeout_text():
    result = (
        "Command completed successfully.\n"
        "Troubleshooting note: Connection timed out can mean a firewall dropped packets.\n"
        + ("line\n" * 400)
        + "final line\n"
    )

    summary = ExecutionEngine._summarize_tool_result_for_context("exec", result)

    assert "Output truncated for context" in summary
    assert "Timeout/Error summary:" not in summary
    assert "Troubleshooting note: Connection timed out" not in summary.split("output start:", 1)[0]
    assert "output tail:" in summary


def test_exec_tool_result_slimming_marks_structured_failure_summary():
    result = json.dumps(
        {
            "ok": False,
            "error": "structured exec failure",
            "output": "line\n" * 500,
        }
    )

    summary = ExecutionEngine._summarize_tool_result_for_context("exec", result)

    assert "Output truncated for context" in summary
    assert "Error summary:" in summary
    assert "structured exec failure" in summary


def test_exec_tool_result_slimming_prefers_tail_lines_for_long_output():
    result = "\n".join([f"line {i}" for i in range(300)])

    summary = ExecutionEngine._summarize_tool_result_for_context("exec", result)

    assert "output start:" in summary
    assert "line 0" in summary
    assert "output tail:" in summary
    assert "line 299" in summary
    assert len(summary) <= ExecutionEngine.EXEC_RESULT_MAX_CHARS + len("\n... (exec context summary truncated)")


class FakeConfigureSkillTool(Tool):
    """Minimal stand-in for configure_skill (mutation success path)."""

    @property
    def name(self) -> str:
        return "configure_skill"

    @property
    def description(self) -> str:
        return "configure skill"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
            },
            "required": ["action"],
        }

    async def _execute(self, action: str, **kwargs):
        return "Added skill 'demo-skill' at /tmp/SKILL.md"


def test_execution_refreshes_system_and_tools_after_configure_skill_success():
    registry = ToolRegistry()
    registry.register(FakeConfigureSkillTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="need tool",
                model="fake-model",
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="configure_skill",
                        arguments={"action": "add"},
                    )
                ],
            ),
            LLMResponse(content="done", model="fake-model"),
        ]
    )
    save_calls = []
    engine = _make_engine(provider, registry, save_calls)
    messages = [
        ChatMessage(role="system", content="SYSTEM_V1"),
        ChatMessage(role="user", content="hi"),
    ]
    state = {"n": 0}

    def refresh_system():
        state["n"] += 1
        return f"SYSTEM_V{state['n'] + 1}"

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=True,
            tool_result_session_id="chat-1",
            refresh_system_prompt=refresh_system,
        )
    )

    assert result.content == "done"
    assert result.used_configure_skill is True
    assert result.executed_tool_calls == 1
    assert messages[0].content == "SYSTEM_V2"
    assert len(provider.calls) == 2
    assert provider.calls[1]["messages"][0].content == "SYSTEM_V2"
    second_tools = provider.calls[1]["tools"]
    assert second_tools is not None
    assert any(t["function"]["name"] == "configure_skill" for t in second_tools)


def test_execution_respects_max_tool_iterations_override():
    registry = ToolRegistry()
    registry.register(DummyTool())
    provider = FakeProvider(
        [
            LLMResponse(
                content="loop",
                model="fake-model",
                tool_calls=[ToolCall(id="tc1", name="demo_tool", arguments={"value": "abc"})],
            )
        ]
    )
    engine = _make_engine(provider, registry, [], tools_config=ToolsConfig(max_tool_iterations=99))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="hi")],
            allow_tools=True,
            max_tool_iterations=1,
        )
    )

    assert "超過了最大迭代次數（1次）" in result.content
    assert result.executed_tool_calls == 1


def test_execution_proactively_compacts_before_llm_request_when_near_budget():
    provider = FakeProvider([LLMResponse(content="after compact", model="fake-model")])
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_compaction_enabled=True,
        context_compaction_token_budget=120,
        context_window_tokens=200,
        context_output_reserve_tokens=80,
        context_compaction_threshold_ratio=0.5,
        context_compaction_min_messages=3,
    )
    statuses = []
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail " + "A" * 12000),
        ChatMessage(role="assistant", content="intermediate answer"),
        ChatMessage(role="user", content="latest instruction"),
    ]

    async def status_hook(update):
        statuses.append(update)

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=False,
            on_llm_status=status_hook,
            work_state_summary="## Structured Work State\n- Objective: Finish compaction handoff\n- Resume hint: Continue validation",
        )
    )

    assert result.content == "after compact"
    assert result.context_compactions == 1
    assert len(result.context_compaction_events) == 1
    event = result.context_compaction_events[0]
    assert event.trigger == "proactive"
    assert event.strategy == "deterministic"
    assert event.outcome == "compacted"
    assert event.iteration == 1
    assert event.messages_before == 4
    assert event.messages_after == 4
    assert event.budget_tokens == 120
    assert event.context_window_tokens == 200
    assert event.output_reserve_tokens == 80
    assert event.threshold_tokens == 60
    assert event.estimated_tokens is not None and event.estimated_tokens > event.threshold_tokens
    assert event.compacted_tokens is not None and event.compacted_tokens < event.estimated_tokens
    assert event.tool_schema_tokens == 0
    assert len(provider.calls) == 1
    sent_messages = provider.calls[0]["messages"]
    assert [message.role for message in sent_messages] == ["system", "system", "assistant", "user"]
    assert sent_messages[0].content == "SYSTEM"
    assert "# Compacted Conversation State" in sent_messages[1].content
    assert "approaching the configured context budget" in sent_messages[1].content
    assert "handoff from a previous context window" in sent_messages[1].content
    assert "Treat summarized older context as reference only" in sent_messages[1].content
    assert "This handoff is not completion evidence" in sent_messages[1].content
    assert "## Structured Work State" in sent_messages[1].content
    assert "## Preserved Recent Tail" in sent_messages[1].content
    assert "latest instruction" in sent_messages[1].content
    assert "A" * 2000 not in sent_messages[1].content
    assert result.compaction_handoff is not None
    assert "Finish compaction handoff" in result.compaction_handoff
    assert "This handoff is not completion evidence" in result.compaction_handoff
    assert sent_messages[2].content == "intermediate answer"
    assert sent_messages[3].content == "latest instruction"
    assert statuses == [
        {
            "message": ExecutionEngine.PROACTIVE_CONTEXT_COMPACTION_STATUS_MESSAGE,
            "status": "compacting",
            "trigger": "proactive_context_compaction",
        }
    ]


def test_execution_skips_proactive_compaction_below_budget():
    provider = FakeProvider([LLMResponse(content="done", model="fake-model")])
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_compaction_enabled=True,
        context_compaction_token_budget=10000,
        context_compaction_threshold_ratio=0.9,
        context_compaction_min_messages=3,
    )
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail"),
        ChatMessage(role="assistant", content="intermediate answer"),
        ChatMessage(role="user", content="latest instruction"),
    ]

    result = asyncio.run(engine.execute_messages("chat-1", messages, allow_tools=False))

    assert result.content == "done"
    assert result.context_compactions == 0
    assert result.context_compaction_events == []
    assert len(provider.calls) == 1
    assert provider.calls[0]["messages"] == messages


def test_execution_uses_llm_compactor_when_configured():
    provider = LlmCompactionProvider(
        "# Compacted Task State\n## Current Goal\nContinue the latest instruction with the important old detail.",
        "after llm compact",
    )
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_compaction_enabled=True,
        context_compaction_token_budget=120,
        context_compaction_threshold_ratio=0.5,
        context_compaction_min_messages=3,
        context_compaction_strategy="llm",
        context_compaction_llm=Config.load_agent_template_config().context_compaction_llm,
    )
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail " + "A" * 12000),
        ChatMessage(role="assistant", content="intermediate answer"),
        ChatMessage(role="user", content="latest instruction"),
    ]

    result = asyncio.run(engine.execute_messages("chat-1", messages, allow_tools=False))

    assert result.content == "after llm compact"
    assert result.context_compactions == 1
    assert len(result.context_compaction_events) == 1
    event = result.context_compaction_events[0]
    assert event.trigger == "proactive"
    assert event.strategy == "llm"
    assert event.outcome == "compacted"
    assert event.fallback_reason is None
    assert event.messages_before == 4
    assert event.messages_after == 4
    assert len(provider.calls) == 2
    compactor_call = provider.calls[0]
    assert compactor_call["tools"] is None
    assert compactor_call["max_tokens"] == 4096
    assert compactor_call["messages"][0].role == "system"
    assert "context compaction engine" in compactor_call["messages"][0].content
    assert "Preserve verification requirements" in compactor_call["messages"][0].content
    sent_messages = provider.calls[1]["messages"]
    assert [message.role for message in sent_messages] == ["system", "system", "assistant", "user"]
    assert sent_messages[0].content == "SYSTEM"
    assert "compacted by an LLM" in sent_messages[1].content
    assert "## Current Goal" in sent_messages[1].content
    assert "A" * 2000 not in sent_messages[1].content
    assert sent_messages[2].content == "intermediate answer"
    assert sent_messages[3].content == "latest instruction"


def test_execution_falls_back_when_llm_compactor_returns_empty():
    provider = LlmCompactionProvider("   ", "after fallback compact")
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_compaction_enabled=True,
        context_compaction_token_budget=120,
        context_compaction_threshold_ratio=0.5,
        context_compaction_min_messages=3,
        context_compaction_strategy="llm",
        context_compaction_llm=Config.load_agent_template_config().context_compaction_llm,
    )
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail " + "A" * 12000),
        ChatMessage(role="assistant", content="intermediate answer"),
        ChatMessage(role="user", content="latest instruction"),
    ]

    result = asyncio.run(engine.execute_messages("chat-1", messages, allow_tools=False))

    assert result.content == "after fallback compact"
    assert result.context_compactions == 1
    assert len(result.context_compaction_events) == 1
    event = result.context_compaction_events[0]
    assert event.trigger == "proactive"
    assert event.strategy == "deterministic"
    assert event.outcome == "fallback"
    assert event.fallback_reason == LLM_COMPACTION_EMPTY_REASON
    assert len(provider.calls) == 2
    sent_messages = provider.calls[1]["messages"]
    assert "approaching the configured context budget" in sent_messages[1].content
    assert "compacted by an LLM" not in sent_messages[1].content


def test_proactive_compaction_does_not_consume_overflow_retry():
    provider = OverflowThenSuccessProvider("after overflow retry")
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_compaction_enabled=True,
        context_compaction_token_budget=120,
        context_compaction_threshold_ratio=0.5,
        context_compaction_min_messages=3,
    )
    statuses = []
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail " + "A" * 12000),
        ChatMessage(role="assistant", content="intermediate answer"),
        ChatMessage(role="user", content="latest instruction"),
    ]

    async def status_hook(update):
        statuses.append(update)

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=False,
            on_llm_status=status_hook,
        )
    )

    assert result.content == "after overflow retry"
    assert result.context_compactions == 2
    assert [event.trigger for event in result.context_compaction_events] == ["proactive", "overflow"]
    assert [event.strategy for event in result.context_compaction_events] == ["deterministic", "deterministic"]
    assert len(provider.calls) == 2
    assert statuses == [
        {
            "message": ExecutionEngine.PROACTIVE_CONTEXT_COMPACTION_STATUS_MESSAGE,
            "status": "compacting",
            "trigger": "proactive_context_compaction",
        },
        {
            "message": ExecutionEngine.CONTEXT_OVERFLOW_STATUS_MESSAGE,
            "status": "compacting",
            "trigger": "context_overflow",
        },
    ]


def test_execution_compacts_and_retries_after_context_overflow():
    provider = OverflowThenSuccessProvider("after compact")
    engine = _make_engine(provider, ToolRegistry(), [])
    statuses = []
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail " + "A" * 5000),
        ChatMessage(role="assistant", content="intermediate answer"),
        ChatMessage(role="tool", content="tool result " + "B" * 5000, tool_call_id="tc1"),
        ChatMessage(role="user", content="latest instruction"),
    ]

    async def status_hook(update):
        statuses.append(update)

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            messages,
            allow_tools=False,
            on_llm_status=status_hook,
        )
    )

    assert result.content == "after compact"
    assert result.context_compactions == 1
    assert len(result.context_compaction_events) == 1
    event = result.context_compaction_events[0]
    assert event.trigger == "overflow"
    assert event.strategy == "deterministic"
    assert event.outcome == "compacted"
    assert event.iteration == 1
    assert event.messages_before == 5
    assert event.messages_after == 4
    assert event.estimated_tokens is not None
    assert event.compacted_tokens is not None and event.compacted_tokens < event.estimated_tokens
    assert "maximum context length" in (event.error or "")
    assert len(provider.calls) == 2
    retried_messages = provider.calls[1]["messages"]
    assert [message.role for message in retried_messages] == ["system", "system", "tool", "user"]
    assert retried_messages[0].content == "SYSTEM"
    assert "# Compacted Conversation State" in retried_messages[1].content
    assert "handoff from a previous context window" in retried_messages[1].content
    assert "## Preserved Recent Tail" in retried_messages[1].content
    assert "latest instruction" in retried_messages[1].content
    assert "A" * 5000 not in retried_messages[1].content
    assert retried_messages[2].tool_call_id == "tc1"
    assert retried_messages[3].content == "latest instruction"
    assert statuses == [
        {
            "message": ExecutionEngine.CONTEXT_OVERFLOW_STATUS_MESSAGE,
            "status": "compacting",
            "trigger": "context_overflow",
        }
    ]


def test_execution_context_overflow_uses_configured_markers():
    provider = OverflowThenSuccessProvider("after custom marker", error_message="provider custom-window exhausted")
    engine = _make_engine(
        provider,
        ToolRegistry(),
        [],
        context_overflow_error_markers=("custom-window",),
    )
    messages = [
        ChatMessage(role="system", content="SYSTEM"),
        ChatMessage(role="user", content="old detail " + "A" * 5000),
        ChatMessage(role="assistant", content="intermediate answer"),
        ChatMessage(role="user", content="latest instruction"),
    ]

    result = asyncio.run(engine.execute_messages("chat-1", messages, allow_tools=False))

    assert result.content == "after custom marker"
    assert result.context_compactions == 1
    assert len(provider.calls) == 2


def test_execution_context_compaction_does_not_consume_tool_iteration():
    class ToolThenOverflowProvider:
        def __init__(self):
            self.calls = []

        async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
            self.calls.append({"messages": list(messages), "tools": tools})
            if len(self.calls) == 1:
                return LLMResponse(
                    content="need tool",
                    model="fake-model",
                    tool_calls=[ToolCall(id="tc1", name="demo_tool", arguments={"value": "abc"})],
                )
            if len(self.calls) == 2:
                raise RuntimeError("context_length_exceeded")
            return LLMResponse(content="done", model="fake-model")

    registry = ToolRegistry()
    registry.register(DummyTool())
    provider = ToolThenOverflowProvider()
    save_calls = []
    engine = _make_engine(provider, registry, save_calls, tools_config=ToolsConfig(max_tool_iterations=2))

    result = asyncio.run(
        engine.execute_messages(
            "chat-1",
            [ChatMessage(role="user", content="hi")],
            allow_tools=True,
            tool_result_session_id="chat-1",
        )
    )

    assert result.content == "done"
    assert result.executed_tool_calls == 1
    assert result.context_compactions == 1
    assert len(provider.calls) == 3
    assert save_calls == [
        ("chat-1", "tool", "tool:abc", "demo_tool", {"tool_args": {"value": "abc"}})
    ]


def test_execution_does_not_compact_unrelated_llm_errors():
    class BrokenProvider:
        async def chat(self, messages, tools=None, model=None, max_tokens=2048, **kwargs):
            raise RuntimeError("network unavailable")

    engine = _make_engine(BrokenProvider(), ToolRegistry(), [])

    try:
        asyncio.run(engine.execute_messages("chat-1", [ChatMessage(role="user", content="hi")], allow_tools=False))
    except RuntimeError as exc:
        assert str(exc) == "network unavailable"
    else:
        raise AssertionError("expected RuntimeError")
