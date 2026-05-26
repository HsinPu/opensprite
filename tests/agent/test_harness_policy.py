from opensprite.agent.harness_policy import HarnessPolicyService
from opensprite.agent.harness_profile import HarnessProfileService
from opensprite.agent.task_contract import TaskContractService, semantic_contract_skip_reason
from opensprite.agent.task_intent import TaskIntentService
from opensprite.tools.base import Tool
from opensprite.tools.permissions import ToolPermissionPolicy
from opensprite.tools.registry import ToolRegistry


class DummyTool(Tool):
    def __init__(self, name: str, *, risk_levels: frozenset[str] | None = None):
        self._name = name
        self._risk_levels = risk_levels

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Dummy {self._name} tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def risk_levels(self) -> frozenset[str] | None:
        return self._risk_levels

    async def _execute(self, **kwargs) -> str:
        return "ok"


def _policy(text: str):
    intent = TaskIntentService().classify(text)
    profile = HarnessProfileService().select(intent)
    return HarnessPolicyService().select(profile)


def test_chat_harness_policy_is_read_only():
    policy = _policy("為什麼 Harness 會讓 AI 更穩？")
    permission_policy = policy.to_permission_policy()

    assert policy.name == "chat_read_policy"
    assert permission_policy.is_tool_exposed("read_file") is True
    assert permission_policy.is_tool_exposed("web_search") is False
    assert permission_policy.is_tool_exposed("edit_file") is False


def test_no_web_constraint_keeps_summary_in_chat_profile():
    intent = TaskIntentService().classify("用三點列出 OpenSprite 可以幫使用者做什麼，不要上網。")
    profile = HarnessProfileService().select(intent)
    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert profile.name == "chat"
    assert contract.requirements == ()
    assert semantic_contract_skip_reason(
        current_message=intent.objective,
        task_intent=intent,
        deterministic_contract=contract,
    ) == "user explicitly disabled web/search evidence"


def test_translation_and_runtime_context_stay_chat_profile():
    translate_intent = TaskIntentService().classify("請把這句翻成英文：今天我想測試 CLI 對話流程。")
    context_intent = TaskIntentService().classify("請回答你目前看到的 channel、session id、current time。")

    assert HarnessProfileService().select(translate_intent).name == "chat"
    assert HarnessProfileService().select(context_intent).name == "chat"


def test_generic_python_debug_question_does_not_require_workspace_or_web():
    intent = TaskIntentService().classify("請說明如果我要 debug Python ModuleNotFoundError，前三個檢查步驟是什麼，不要上網。")
    profile = HarnessProfileService().select(intent)
    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert profile.name == "chat"
    assert contract.requirements == ()


def test_research_harness_policy_allows_web_without_workspace_mutation():
    policy = _policy("幫我查一下最新消息並附來源")
    permission_policy = policy.to_permission_policy()

    assert policy.name == "research_source_policy"
    assert "max_tool_iterations" not in policy.to_metadata()
    assert permission_policy.is_tool_exposed("web_search") is True
    assert permission_policy.is_tool_exposed("web_fetch") is True
    assert permission_policy.is_tool_exposed("edit_file") is False
    assert permission_policy.is_tool_exposed("browser_click") is False


def test_coding_change_policy_requires_approval_for_configuration_tools():
    policy = _policy("Please fix the failing pytest in src/opensprite/agent/task_intent.py")
    permission_policy = policy.to_permission_policy()

    assert policy.name == "workspace_change_policy"
    assert permission_policy.is_tool_exposed("edit_file") is True
    assert permission_policy.is_tool_exposed("verify") is True
    decision = permission_policy.check("configure_skill", {})
    assert decision.allowed is False
    assert decision.requires_approval is True


def test_coding_analysis_policy_blocks_write_and_execute_tools():
    policy = _policy("Review src/opensprite/agent/task_intent.py and explain the logic")
    permission_policy = policy.to_permission_policy()

    assert policy.name == "workspace_analysis_policy"
    assert permission_policy.is_tool_exposed("read_file") is True
    assert permission_policy.is_tool_exposed("edit_file") is False
    assert permission_policy.is_tool_exposed("verify") is False


def test_ops_policy_requires_approval_for_external_side_effects_and_mcp():
    policy = _policy("Update the MCP server configuration and restart the service")
    permission_policy = policy.to_permission_policy()

    assert policy.name == "operations_approval_policy"
    assert permission_policy.is_tool_exposed("credential_store") is True
    mcp_decision = permission_policy.check("mcp_example_tool", {})
    assert mcp_decision.allowed is False
    assert mcp_decision.requires_approval is True
    browser_decision = permission_policy.check("browser_click", {})
    assert browser_decision.allowed is False
    assert browser_decision.requires_approval is True


def test_media_policy_allows_media_tools_without_workspace_writes():
    policy = _policy("請分析這張圖片並做 OCR")
    permission_policy = policy.to_permission_policy()

    assert policy.name == "media_artifact_policy"
    assert permission_policy.is_tool_exposed("analyze_image") is True
    assert permission_policy.is_tool_exposed("ocr_image") is True
    assert permission_policy.is_tool_exposed("edit_file") is False


def test_harness_policy_filters_tool_registry_for_research_turns():
    registry = ToolRegistry()
    for name in ("read_file", "web_search", "web_fetch", "edit_file", "verify"):
        registry.register(DummyTool(name))
    policy = _policy("Search the web and cite sources for the latest release")

    filtered = HarnessPolicyService().build_tool_registry(registry, policy)

    assert "read_file" in filtered.tool_names
    assert "web_search" in filtered.tool_names
    assert "web_fetch" in filtered.tool_names
    assert "edit_file" not in filtered.tool_names
    assert "verify" not in filtered.tool_names


def test_chat_harness_policy_uses_declared_read_only_tool_metadata():
    registry = ToolRegistry()
    registry.register(DummyTool("custom_read", risk_levels=frozenset({"read"})))
    registry.register(DummyTool("custom_unknown"))
    policy = _policy("hello")

    filtered = HarnessPolicyService().build_tool_registry(registry, policy)

    assert filtered.tool_names == ["custom_read"]


def test_profile_permission_override_is_composed_with_harness_policy():
    registry = ToolRegistry()
    registry.register(DummyTool("read_file", risk_levels=frozenset({"read"})))
    registry.register(DummyTool("web_fetch", risk_levels=frozenset({"network"})))
    policy = _policy("Search the web and cite sources for the latest release")
    profile_override = ToolPermissionPolicy(allowed_risk_levels=["read"])

    filtered = HarnessPolicyService().build_tool_registry(registry, policy, profile_override)

    assert filtered.tool_names == ["read_file"]


def test_harness_policy_resolution_metadata_explains_blocked_relaxations():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(approval_mode="auto", allowed_risk_levels=["read", "write", "configuration", "mcp"])
    )
    policy = _policy("Update the MCP server configuration and restart the service")
    profile_override = ToolPermissionPolicy(allowed_risk_levels=["read", "write", "mcp"], approval_mode="auto")

    filtered = HarnessPolicyService().build_tool_registry(registry, policy, profile_override)

    metadata = filtered.permission_resolution_metadata
    assert metadata is not None
    assert metadata["global_policy"]["approval_mode"] == "auto"
    assert metadata["profile_override"]["approval_mode"] == "auto"
    assert metadata["harness_policy"]["name"] == "operations_approval_policy"
    assert metadata["effective_policy"]["kind"] == "composite"
    assert "profile permission override" in metadata["constraints_applied"]
    assert any(item["field"] == "approval_mode" for item in metadata["blocked_relaxations"])
