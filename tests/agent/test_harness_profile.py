from opensprite.agent.harness_profile import HarnessProfileService
from opensprite.agent.task_intent import TaskIntentService


def _profile(text: str):
    intent = TaskIntentService().classify(text)
    return HarnessProfileService().select(intent)


def test_harness_profile_selects_research_for_url_task():
    profile = _profile("幫我查一下這個網站 https://example.com 並整理來源")

    assert profile.name == "research"
    assert profile.task_type == "web_research"
    assert "web_source" in profile.required_evidence
    assert profile.verification_policy == "source_grounded"


def test_harness_profile_selects_coding_for_code_change_task():
    profile = _profile("Please fix the failing pytest in src/opensprite/agent/task_intent.py")

    assert profile.name == "coding"
    assert profile.task_type == "workspace_change"
    assert "workspace_read" in profile.required_tool_groups
    assert "workspace_write" in profile.required_tool_groups
    assert profile.verification_policy == "focused_if_possible"


def test_harness_profile_selects_ops_before_coding_for_configuration_task():
    profile = _profile("Update the MCP server configuration and restart the service")

    assert profile.name == "ops"
    assert profile.continuation_policy == "approval_bounded"
    assert "configuration" in profile.approval_required_risk_levels


def test_harness_profile_selects_chat_for_plain_question():
    profile = _profile("為什麼 Harness 會讓 AI 更穩？")

    assert profile.name == "chat"
    assert profile.continuation_policy == "minimal"
