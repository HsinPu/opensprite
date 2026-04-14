from opensprite.llms.tool_args import parse_tool_arguments


def test_parse_tool_arguments_accepts_json_object_string():
    result = parse_tool_arguments(
        '{"path": "notes.txt", "content": "hello"}',
        provider_name="TestProvider",
        tool_name="write_file",
    )

    assert result == {"path": "notes.txt", "content": "hello"}


def test_parse_tool_arguments_returns_empty_dict_for_invalid_json_string():
    result = parse_tool_arguments(
        '{"path": "notes.txt",',
        provider_name="TestProvider",
        tool_name="write_file",
    )

    assert result == {}


def test_parse_tool_arguments_returns_empty_dict_for_non_object_json():
    result = parse_tool_arguments(
        '["notes.txt"]',
        provider_name="TestProvider",
        tool_name="read_file",
    )

    assert result == {}
