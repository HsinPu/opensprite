import json

from opensprite.agent.subagent_output import parse_structured_subagent_output


def test_parse_structured_subagent_output_extracts_trailing_json_block():
    payload = {
        "schema_version": 1,
        "contract": "readonly_subagent_result",
        "prompt_type": "code-reviewer",
        "status": "ok",
        "summary": "One high-risk issue found.",
        "sections": [
            {
                "key": "findings",
                "title": "Review Findings",
                "type": "finding_list",
                "items": [
                    {
                        "title": "Race condition",
                        "severity": "high",
                        "path": "src/foo.py",
                        "start_line": 10,
                        "end_line": 18,
                        "why": "Concurrent mutation may corrupt state.",
                        "fix": "Guard writes with one lock.",
                    }
                ],
            }
        ],
        "questions": ["Need production traffic pattern?"],
        "residual_risks": ["Did not inspect deployment config."],
        "sources": [{"kind": "file", "path": "src/foo.py", "start_line": 10, "end_line": 18}],
    }
    text = (
        "Review Findings\n"
        "1. high src/foo.py: Race condition\n"
        "   Why: Concurrent mutation may corrupt state.\n"
        "   Fix: Guard writes with one lock.\n\n"
        "```json\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )

    visible_text, structured_output, parse_error = parse_structured_subagent_output(
        text,
        prompt_type="code-reviewer",
    )

    assert parse_error is None
    assert visible_text.startswith("Review Findings")
    assert "```json" not in visible_text
    assert structured_output is not None
    assert structured_output["prompt_type"] == "code-reviewer"
    assert structured_output["status"] == "ok"
    assert structured_output["section_count"] == 1
    assert structured_output["finding_count"] == 1
    assert structured_output["question_count"] == 1
    assert structured_output["residual_risk_count"] == 1


def test_parse_structured_subagent_output_falls_back_on_invalid_json_block():
    text = (
        "Research summary\n"
        "\n"
        "```json\n"
        '{"schema_version": 1, "contract": "readonly_subagent_result", invalid}\n'
        "```\n"
    )

    visible_text, structured_output, parse_error = parse_structured_subagent_output(
        text,
        prompt_type="researcher",
    )

    assert visible_text == "Research summary"
    assert structured_output is None
    assert parse_error == "invalid_json: Expecting property name enclosed in double quotes"


def test_parse_structured_subagent_output_rejects_prompt_type_mismatch():
    payload = {
        "schema_version": 1,
        "contract": "readonly_subagent_result",
        "prompt_type": "researcher",
        "status": "ok",
        "summary": "ok",
    }
    text = f"Research summary\n\n```json\n{json.dumps(payload)}\n```"

    visible_text, structured_output, parse_error = parse_structured_subagent_output(
        text,
        prompt_type="code-reviewer",
    )

    assert visible_text == "Research summary"
    assert structured_output is None
    assert parse_error == "prompt_type_mismatch"


def test_parse_structured_subagent_output_preserves_plain_text_when_no_json_block():
    visible_text, structured_output, parse_error = parse_structured_subagent_output(
        "No structured output here.",
        prompt_type="outliner",
    )

    assert visible_text == "No structured output here."
    assert structured_output is None
    assert parse_error is None
