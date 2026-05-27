from opensprite.agent.harness_inventory import build_harness_inventory, expected_sensor_ids_for_task_type, harness_inventory_payload


def test_harness_inventory_uses_representative_profiles_and_policies():
    items = {item.key: item for item in build_harness_inventory()}

    assert set(items) == {
        "chat:conversation",
        "research:web_research",
        "coding:workspace_analysis",
        "coding:workspace_change",
        "media:media_extraction",
        "ops:operations",
    }
    assert items["chat:conversation"].policy_name == "chat_read_policy"
    assert items["research:web_research"].policy_name == "research_source_policy"
    assert items["coding:workspace_analysis"].policy_name == "workspace_analysis_policy"
    assert items["coding:workspace_change"].policy_name == "workspace_change_policy"
    assert items["media:media_extraction"].policy_name == "media_artifact_policy"
    assert items["ops:operations"].policy_name == "operations_approval_policy"


def test_harness_inventory_declares_expected_sensor_ids():
    items = {item.key: item for item in build_harness_inventory()}

    assert "chat.no_unexpected_tools" in items["chat:conversation"].expected_sensor_ids
    assert "research.source_coverage" in items["research:web_research"].expected_sensor_ids
    assert "coding.verification" in items["coding:workspace_change"].expected_sensor_ids
    assert "ops.approval_boundary" in items["ops:operations"].expected_sensor_ids
    assert expected_sensor_ids_for_task_type("question") == items["chat:conversation"].expected_sensor_ids


def test_harness_inventory_payload_is_json_safe():
    payload = harness_inventory_payload()

    assert payload["schema_version"] == 1
    assert payload["kind"] == "harness_inventory"
    assert len(payload["items"]) == 6
    assert all(item["key"] for item in payload["items"])
    assert all(isinstance(item["expected_sensor_ids"], list) for item in payload["items"])
