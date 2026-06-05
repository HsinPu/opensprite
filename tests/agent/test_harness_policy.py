from opensprite.agent.harness_policy import (
    CHAT_HARNESS_POLICY_REASON,
    HARNESS_APPROVAL_REQUIREMENT_PROTECTED_REASON,
    MEDIA_HARNESS_POLICY_REASON,
    OPERATIONS_HARNESS_POLICY_REASON,
    POLICY_RESOLUTION_METADATA_REASON,
    RESEARCH_HARNESS_POLICY_REASON,
    WORKSPACE_ANALYSIS_HARNESS_POLICY_REASON,
    WORKSPACE_CHANGE_HARNESS_POLICY_REASON,
    HarnessPolicyService,
)
from opensprite.agent.harness_profile import HarnessProfile
from opensprite.agent.harness_profile import HarnessProfileService
from opensprite.agent.task_contract import EvidenceRequirement, LLM_PLANNER_CONTRACT_SOURCES, TaskContract
from opensprite.agent.task_intent import TaskIntentService
from opensprite.tools.base import Tool
from opensprite.tools.permissions import ToolPermissionPolicy
from opensprite.tools.registry import ToolRegistry
from tests.agent.task_contract_test_helpers import TaskContractService


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
    profile = HarnessProfileService().default_chat_profile()
    return HarnessPolicyService().select(profile)


def _policy_for_contract(task_type: str, *requirements: EvidenceRequirement):
    contract = TaskContract(
        objective="test objective",
        task_type=task_type,
        requirements=tuple(requirements),
        allow_no_tool_final=not requirements,
        contract_sources=LLM_PLANNER_CONTRACT_SOURCES,
    )
    profile = HarnessProfileService().from_contract(contract)
    return HarnessPolicyService().select(profile)


def test_harness_policy_reasons_are_stable():
    assert (
        RESEARCH_HARNESS_POLICY_REASON
        == "research turns should gather source evidence first while using any tools allowed by user permissions"
    )
    assert (
        WORKSPACE_ANALYSIS_HARNESS_POLICY_REASON
        == "workspace analysis turns should inspect context first and avoid unnecessary mutation"
    )
    assert (
        WORKSPACE_CHANGE_HARNESS_POLICY_REASON
        == "workspace change turns should edit carefully and verify while preserving configured approval gates"
    )
    assert (
        MEDIA_HARNESS_POLICY_REASON
        == "media turns should use relevant media extraction tools before finalizing"
    )
    assert OPERATIONS_HARNESS_POLICY_REASON == (
        "operations turns should preserve approval gates for configuration, MCP, or external side effects"
    )
    assert CHAT_HARNESS_POLICY_REASON == "chat turns should answer directly unless tools are useful and allowed"
    assert POLICY_RESOLUTION_METADATA_REASON == (
        "effective policy is the ordered intersection of global permissions, profile override, and harness executable policy"
    )
    assert HARNESS_APPROVAL_REQUIREMENT_PROTECTED_REASON == (
        "harness approval requirements remain active in the executable policy"
    )


def test_chat_harness_policy_guidance_is_read_first():
    policy = _policy("為什麼 Harness 會讓 AI 更穩？")
    permission_policy = policy.to_permission_policy()

    assert policy.name == "chat_guidance_policy"
    assert permission_policy.is_tool_exposed("read_file") is True
    assert permission_policy.is_tool_exposed("web_search") is False
    assert permission_policy.is_tool_exposed("edit_file") is False


def test_no_web_constraint_keeps_summary_in_chat_profile():
    intent = TaskIntentService().classify("用三點列出 OpenSprite 可以幫使用者做什麼，不要上網。")
    profile = HarnessProfileService().default_chat_profile()
    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert profile.name == "chat"
    assert contract.requirements == ()


def test_translation_and_runtime_context_stay_chat_profile():
    translate_intent = TaskIntentService().classify("請把這句翻成英文：今天我想測試 CLI 對話流程。")
    context_intent = TaskIntentService().classify("請回答你目前看到的 channel、session id、current time。")

    assert HarnessProfileService().default_chat_profile().name == "chat"
    assert HarnessProfileService().default_chat_profile().name == "chat"


def test_translation_with_test_word_does_not_require_workspace_verification():
    text = "\u8acb\u628a\u9019\u53e5\u7ffb\u6210\u82f1\u6587\uff1a\u4eca\u5929\u6211\u60f3\u6e2c\u8a66 CLI \u5c0d\u8a71\u6d41\u7a0b\u3002"
    intent = TaskIntentService().classify(text)
    profile = HarnessProfileService().default_chat_profile()
    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert profile.name == "chat"
    assert contract.task_type == "pure_answer"
    assert contract.requirements == ()


def test_generic_python_debug_question_does_not_require_workspace_or_web():
    intent = TaskIntentService().classify("請說明如果我要 debug Python ModuleNotFoundError，前三個檢查步驟是什麼，不要上網。")
    profile = HarnessProfileService().default_chat_profile()
    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert profile.name == "chat"
    assert contract.requirements == ()


def test_standalone_connection_timeout_question_does_not_require_workspace_with_history():
    text = "What can cause Connection timed out?"
    intent = TaskIntentService().classify(text)
    profile = HarnessProfileService().default_chat_profile()
    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        history=[
            {"role": "tool", "tool_name": "read_file", "content": "src/opensprite/agent/task_contract.py"},
            {"role": "assistant", "content": "I inspected task_contract.py."},
        ],
        harness_profile=profile,
    )

    assert profile.name == "chat"
    assert contract.requirements == ()
    assert contract.task_type in {"question", "pure_answer"}


def test_explicit_no_web_and_no_file_constraints_hide_tools():
    text = "\u4e0d\u8981\u8b80\u6a94\u4e5f\u4e0d\u8981\u4e0a\u7db2\uff0c\u53ea\u56de\u7b54\u9019\u662f\u4ec0\u9ebc\u985e\u578b\u7684\u554f\u984c"
    intent = TaskIntentService().classify(text)
    profile = HarnessProfileService().default_chat_profile()
    policy = HarnessPolicyService().select(profile)
    permission_policy = policy.to_permission_policy()
    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert profile.name == "chat"
    assert contract.requirements == ()
    assert permission_policy.is_tool_exposed("web_research") is False
    assert permission_policy.is_tool_exposed("web_search") is False
    assert permission_policy.is_tool_exposed("read_file") is True
    assert permission_policy.is_tool_exposed("list_dir") is True
    assert permission_policy.is_tool_exposed("search_history") is True


def test_compound_english_no_web_and_no_file_constraints_hide_tools():
    text = "Do not read files or use the web. Explain Python ModuleNotFoundError in plain English."
    intent = TaskIntentService().classify(text)
    profile = HarnessProfileService().default_chat_profile()
    policy = HarnessPolicyService().select(profile)
    permission_policy = policy.to_permission_policy()
    contract = TaskContractService.build_deterministic(
        task_intent=intent,
        current_message=intent.objective,
        harness_profile=profile,
    )

    assert profile.name == "chat"
    assert contract.requirements == ()
    assert permission_policy.is_tool_exposed("web_research") is False
    assert permission_policy.is_tool_exposed("web_search") is False
    assert permission_policy.is_tool_exposed("read_file") is True
    assert permission_policy.is_tool_exposed("list_dir") is True


def test_research_harness_policy_allows_web_without_workspace_mutation():
    policy = _policy_for_contract("web_research", EvidenceRequirement(kind="tool_group", tool_group="web_research"))
    permission_policy = policy.to_permission_policy()

    assert policy.name == "research_source_guidance_policy"
    assert "max_tool_iterations" not in policy.to_metadata()
    assert permission_policy.is_tool_exposed("web_search") is True
    assert permission_policy.is_tool_exposed("web_fetch") is True
    assert permission_policy.is_tool_exposed("edit_file") is False
    assert permission_policy.is_tool_exposed("browser_click") is False


def test_coding_change_policy_requires_approval_for_configuration_tools():
    policy = _policy_for_contract("code_change", EvidenceRequirement(kind="tool_group", tool_group="workspace_write"))
    permission_policy = policy.to_permission_policy()

    assert policy.name == "workspace_change_guidance_policy"
    assert permission_policy.is_tool_exposed("edit_file") is True
    assert permission_policy.is_tool_exposed("verify") is True
    decision = permission_policy.check("configure_skill", {})
    assert decision.allowed is False
    assert decision.requires_approval is True


def test_coding_analysis_policy_blocks_write_and_execute_tools():
    policy = _policy_for_contract("workspace_read", EvidenceRequirement(kind="tool_group", tool_group="workspace_read"))
    permission_policy = policy.to_permission_policy()

    assert policy.name == "workspace_analysis_guidance_policy"
    assert permission_policy.is_tool_exposed("read_file") is True
    assert permission_policy.is_tool_exposed("edit_file") is False
    assert permission_policy.is_tool_exposed("verify") is False


def test_ops_policy_requires_approval_for_external_side_effects_and_mcp():
    policy = _policy_for_contract("operations")
    permission_policy = policy.to_permission_policy()

    assert policy.name == "operations_approval_guidance_policy"
    assert permission_policy.is_tool_exposed("credential_store") is True
    mcp_decision = permission_policy.check("mcp_example_tool", {})
    assert mcp_decision.allowed is False
    assert mcp_decision.requires_approval is True
    browser_decision = permission_policy.check("browser_click", {})
    assert browser_decision.allowed is False
    assert browser_decision.requires_approval is True


def test_media_policy_allows_media_tools_without_workspace_writes():
    policy = _policy_for_contract("media_extraction", EvidenceRequirement(kind="tool_group", tool_group="media"))
    permission_policy = policy.to_permission_policy()

    assert policy.name == "media_artifact_guidance_policy"
    assert permission_policy.is_tool_exposed("analyze_image") is True
    assert permission_policy.is_tool_exposed("ocr_image") is True
    assert permission_policy.is_tool_exposed("edit_file") is False


def test_harness_policy_guides_research_turns_without_filtering_global_permissions():
    registry = ToolRegistry()
    for name in ("read_file", "web_search", "web_fetch", "edit_file", "verify"):
        registry.register(DummyTool(name))
    policy = _policy_for_contract("web_research", EvidenceRequirement(kind="tool_group", tool_group="web_research"))

    filtered = HarnessPolicyService().build_tool_registry(registry, policy)

    guidance_policy = policy.to_permission_policy()
    assert guidance_policy.is_tool_exposed("web_search") is True
    assert guidance_policy.is_tool_exposed("edit_file") is False
    assert "read_file" in filtered.tool_names
    assert "web_search" in filtered.tool_names
    assert "web_fetch" in filtered.tool_names
    assert "edit_file" in filtered.tool_names
    assert "verify" in filtered.tool_names


def test_chat_harness_policy_guidance_uses_declared_read_only_tool_metadata():
    registry = ToolRegistry()
    registry.register(DummyTool("custom_read", risk_levels=frozenset({"read"})))
    registry.register(DummyTool("custom_unknown"))
    registry.register(DummyTool("task_update"))
    policy = _policy("hello")

    filtered = HarnessPolicyService().build_tool_registry(registry, policy)

    guidance_policy = policy.to_permission_policy()
    assert guidance_policy.is_tool_exposed("custom_read", tool_risk_levels=frozenset({"read"})) is True
    assert guidance_policy.is_tool_exposed("custom_unknown") is False
    assert guidance_policy.is_tool_exposed("task_update") is False
    assert filtered.tool_names == ["custom_read", "custom_unknown", "task_update"]
    assert filtered.get("custom_read") is not None
    assert filtered.get("custom_unknown") is not None
    assert filtered.get("task_update") is not None


def test_profile_permission_override_is_composed_with_harness_policy():
    registry = ToolRegistry()
    registry.register(DummyTool("read_file", risk_levels=frozenset({"read"})))
    registry.register(DummyTool("web_fetch", risk_levels=frozenset({"network"})))
    policy = _policy_for_contract("web_research", EvidenceRequirement(kind="tool_group", tool_group="web_research"))
    profile_override = ToolPermissionPolicy(allowed_risk_levels=["read"])

    filtered = HarnessPolicyService().build_tool_registry(registry, policy, profile_override)

    assert filtered.tool_names == ["read_file"]


def test_harness_policy_resolution_metadata_explains_protected_approval_requirements():
    registry = ToolRegistry(
        permission_policy=ToolPermissionPolicy(approval_mode="auto", allowed_risk_levels=["read", "write", "configuration", "mcp"])
    )
    policy = _policy_for_contract("operations")
    profile_override = ToolPermissionPolicy(allowed_risk_levels=["read", "write", "mcp"], approval_mode="auto")

    filtered = HarnessPolicyService().build_tool_registry(registry, policy, profile_override)

    metadata = filtered.permission_resolution_metadata
    assert metadata is not None
    assert metadata["global_policy"]["approval_mode"] == "auto"
    assert metadata["profile_override"]["approval_mode"] == "auto"
    assert metadata["harness_policy"]["name"] == "operations_approval_guidance_policy"
    assert metadata["effective_policy"]["kind"] == "composite"
    assert metadata["reason"] == POLICY_RESOLUTION_METADATA_REASON
    assert "profile permission override" in metadata["constraints_applied"]
    approval_protections = [item for item in metadata["protected_approval_requirements"] if item["field"] == "approval_mode"]
    assert approval_protections
    assert all(item["reason"] == HARNESS_APPROVAL_REQUIREMENT_PROTECTED_REASON for item in approval_protections)


def _registry_for_permission_baseline() -> ToolRegistry:
    registry = ToolRegistry()
    for name in (
        "read_file",
        "list_dir",
        "web_search",
        "web_fetch",
        "web_research",
        "apply_patch",
        "exec",
        "task_update",
        "configure_mcp",
        "delegate",
        "analyze_image",
        "send_media",
    ):
        registry.register(DummyTool(name))
    return registry


def _filtered_tool_names(profile: HarnessProfile) -> set[str]:
    policy = HarnessPolicyService().select(profile)
    return set(HarnessPolicyService().build_tool_registry(_registry_for_permission_baseline(), policy).tool_names)


def test_permission_baseline_chat_profile_keeps_global_tool_access():
    tool_names = _filtered_tool_names(HarnessProfile(name="chat", task_type="question"))

    assert {"read_file", "list_dir"}.issubset(tool_names)
    assert "web_search" in tool_names
    assert "web_fetch" in tool_names
    assert "web_research" in tool_names
    assert "apply_patch" in tool_names
    assert "exec" in tool_names
    assert "task_update" in tool_names
    assert "configure_mcp" in tool_names


def test_permission_baseline_research_profile_keeps_global_tool_access():
    tool_names = _filtered_tool_names(HarnessProfile(name="research", task_type="web_research"))

    assert {"read_file", "web_search", "web_fetch", "web_research"}.issubset(tool_names)
    assert "apply_patch" in tool_names
    assert "exec" in tool_names
    assert "task_update" in tool_names
    assert "configure_mcp" in tool_names


def test_permission_baseline_workspace_analysis_profile_keeps_global_tool_access():
    tool_names = _filtered_tool_names(HarnessProfile(name="coding", task_type="workspace_analysis"))

    assert {"read_file", "list_dir", "web_search", "web_fetch", "delegate"}.issubset(tool_names)
    assert "apply_patch" in tool_names
    assert "exec" in tool_names
    assert "task_update" in tool_names
    assert "configure_mcp" in tool_names


def test_permission_baseline_workspace_change_profile_allows_writes_and_asks_for_configuration():
    policy = HarnessPolicyService().select(
        HarnessProfile(
            name="coding",
            task_type="workspace_change",
            approval_required_risk_levels=("external_side_effect", "configuration"),
        )
    )
    filtered = HarnessPolicyService().build_tool_registry(_registry_for_permission_baseline(), policy)
    tool_names = set(filtered.tool_names)

    assert {"read_file", "apply_patch", "exec", "task_update", "delegate"}.issubset(tool_names)
    assert "configure_mcp" in tool_names
    decision = filtered.permission_policy.check("configure_mcp", {})
    assert decision.allowed is False
    assert decision.requires_approval is True


def test_permission_baseline_ops_profile_exposes_approval_gated_tools():
    policy = HarnessPolicyService().select(
        HarnessProfile(
            name="ops",
            task_type="operations",
            approval_required_risk_levels=("external_side_effect", "configuration", "mcp"),
        )
    )
    filtered = HarnessPolicyService().build_tool_registry(_registry_for_permission_baseline(), policy)

    assert "configure_mcp" in filtered.tool_names
    assert "exec" in filtered.tool_names
    decision = filtered.permission_policy.check("configure_mcp", {})
    assert decision.allowed is False
    assert decision.requires_approval is True
