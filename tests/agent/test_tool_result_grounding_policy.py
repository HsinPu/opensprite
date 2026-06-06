from opensprite.agent.quality_gate import response_reports_tool_result_preview


def test_response_reports_tool_result_preview_accepts_exact_preview():
    assert response_reports_tool_result_preview(
        "The command returned git version 2.47.1.windows.2.",
        "git version 2.47.1.windows.2",
    )


def test_response_reports_tool_result_preview_accepts_version_token_overlap():
    assert response_reports_tool_result_preview(
        "2.47.1.windows.2",
        "git version 2.47.1.windows.2",
    )


def test_response_reports_tool_result_preview_rejects_unrelated_response():
    assert not response_reports_tool_result_preview(
        "The command is available.",
        "git version 2.47.1.windows.2",
    )
