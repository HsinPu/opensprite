from pathlib import Path

from opensprite.context.paths import sync_templates
from opensprite.subagent_prompts import load_metadata, load_prompt


def test_core_coding_subagents_are_executable_workflows(tmp_path: Path) -> None:
    app_home = tmp_path / "home"
    sync_templates(app_home, silent=True)

    expectations = {
        "implementer": ('"1.1"', "implementation", ["apply_patch", "Verification", "不要只輸出建議方案"]),
        "debugger": ('"1.1"', "implementation", ["重現", "Root Cause", "直接修正"]),
        "code-reviewer": ('"1.2"', "read-only", ["Review Findings", "實際變更", "一般 review 預設只回報 findings"]),
        "test-writer": ('"1.1"', "testing", ["直接新增或修改測試", "Verification", "regression test"]),
    }

    for prompt_type, (expected_version, expected_profile, required_snippets) in expectations.items():
        metadata = load_metadata(prompt_type, app_home=app_home)
        prompt = load_prompt(prompt_type, app_home=app_home)

        assert metadata["version"] == expected_version
        assert metadata["tool_profile"] == expected_profile
        assert "## 角色（Role）" in prompt
        assert "## 任務（Task）" in prompt
        assert "## 規範（Constraints）" in prompt
        assert "## 輸出（Output）" in prompt
        assert "工具" in prompt or "tool" in prompt.lower()
        for snippet in required_snippets:
            assert snippet in prompt
