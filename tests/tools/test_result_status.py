import json

from opensprite.tools.result_status import classify_tool_result_status, tool_error_result


def test_tool_result_status_allows_successful_batch_payload():
    status = classify_tool_result_status(
        json.dumps(
            {
                "type": "batch",
                "ok": True,
                "summary": "Batch completed: 2 call(s), 0 failed.",
                "total": 2,
                "failed": 0,
                "results": [],
            }
        )
    )

    assert status.ok is True
    assert status.error_metadata() == {}


def test_tool_result_status_flags_failed_batch_payload():
    status = classify_tool_result_status(
        json.dumps(
            {
                "type": "batch",
                "ok": False,
                "summary": "Batch completed: 2 call(s), 1 failed.",
                "total": 2,
                "failed": 1,
                "error": "Batch completed: 2 call(s), 1 failed.",
                "error_type": "ToolFailure",
                "category": "batch_failure",
                "results": [],
            }
        )
    )

    assert status.ok is False
    assert status.error_type == "ToolFailure"
    assert status.category == "batch_failure"


def test_tool_result_status_honors_structured_error_payload():
    status = classify_tool_result_status(json.dumps({"type": "web_search", "ok": False, "error": "Search failed"}))

    assert status.ok is False
    assert status.error_metadata() == {"error": "Search failed", "error_type": "ToolError"}


def test_tool_result_status_honors_structured_error_metadata():
    status = classify_tool_result_status(
        tool_error_result(
            "Tool 'exec' blocked by permission policy.",
            error_type="ToolPermissionError",
            category="permission_block",
            repeated_error_key="permission:exec",
            invalid_arguments=True,
        )
    )

    assert status.ok is False
    assert status.error_type == "ToolPermissionError"
    assert status.category == "permission_block"
    assert status.repeated_error_key == "permission:exec"
    assert status.invalid_arguments is True
    assert status.error_metadata()["category"] == "permission_block"


def test_tool_result_status_extracts_structured_execution_error_metadata():
    status = classify_tool_result_status(
        tool_error_result(
            "HTTP Error: 404 Not Found",
            error_type="ToolExecutionError",
            metadata={"tool_name": "web_fetch"},
        )
    )

    assert status.ok is False
    assert status.error_metadata() == {
        "error": "HTTP Error: 404 Not Found",
        "error_type": "ToolExecutionError",
        "status_code": 404,
    }


def test_tool_result_status_exposes_invalid_arguments_repeat_key():
    result = "Error: Invalid arguments for web_fetch: url is required"
    status = classify_tool_result_status(result)

    assert status.ok is False
    assert status.invalid_arguments is True
    assert status.repeated_error_key == result


def test_tool_result_status_exposes_structured_invalid_arguments_repeat_key():
    result = tool_error_result(
        "Invalid arguments for web_fetch: url is required.",
        error_type="ToolValidationError",
        category="invalid_arguments",
        repeated_error_key="Invalid arguments for web_fetch: url is required.",
        invalid_arguments=True,
    )
    status = classify_tool_result_status(result)

    assert status.ok is False
    assert status.error_type == "ToolValidationError"
    assert status.category == "invalid_arguments"
    assert status.invalid_arguments is True
    assert status.repeated_error_key == "Invalid arguments for web_fetch: url is required."


def test_tool_result_status_classifies_permission_blocks():
    result = tool_error_result(
        "Tool 'exec' blocked by permission policy: tool 'exec' is listed in denied_tools.",
        error_type="ToolPermissionError",
        category="permission_block",
    )
    status = classify_tool_result_status(result)

    assert status.ok is False
    assert status.error_type == "ToolPermissionError"
    assert status.category == "permission_block"


def test_tool_result_status_keeps_incidental_failure_words_successful():
    text = "What can cause Connection timed out? The startup failed retry phrase is just content."
    status = classify_tool_result_status(text)

    assert status.ok is True
    assert status.error_metadata() == {}
