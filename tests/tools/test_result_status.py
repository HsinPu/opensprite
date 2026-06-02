import json

from opensprite.tools.result_status import classify_tool_result_status


def test_tool_result_status_allows_successful_batch_summary():
    status = classify_tool_result_status("Batch completed: 2 call(s), 0 failed.\n\n[1] list_dir\nok")

    assert status.ok is True
    assert status.error_metadata() == {}


def test_tool_result_status_flags_failed_batch_summary():
    status = classify_tool_result_status("Batch completed: 2 call(s), 1 failed.\n\n[1] read_file\nError: missing")

    assert status.ok is False
    assert status.error_type == "ToolFailure"


def test_tool_result_status_honors_structured_error_payload():
    status = classify_tool_result_status(json.dumps({"type": "web_search", "ok": False, "error": "Search failed"}))

    assert status.ok is False
    assert status.error_metadata() == {"error": "Search failed", "error_type": "ToolError"}


def test_tool_result_status_extracts_error_executing_metadata():
    status = classify_tool_result_status("Error executing web_fetch: HTTP Error: 404 Not Found")

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


def test_tool_result_status_classifies_permission_blocks():
    result = "Error: Tool 'exec' blocked by permission policy: tool 'exec' is listed in denied_tools."
    status = classify_tool_result_status(result)

    assert status.ok is False
    assert status.error_type == "ToolPermissionError"
    assert status.category == "permission_block"
