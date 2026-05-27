from opensprite.agent.task_intent import TaskIntentService


def test_task_intent_classifier_marks_refactor_as_verifiable_long_task():
    intent = TaskIntentService().classify(
        "Please refactor the agent and run tests. Keep the public API stable."
    )

    assert intent.kind == "refactor"
    assert intent.long_running is True
    assert intent.should_seed_active_task is True
    assert "relevant tests or checks pass, or the verification gap is stated" in intent.done_criteria
    assert intent.verification_hint == "Run the requested verification and report pass or fail."
    assert intent.expects_code_change is True
    assert intent.expects_verification is True
    assert intent.constraints == ("Keep the public API stable.",)


def test_task_intent_classifier_keeps_question_out_of_active_task_seed():
    intent = TaskIntentService().classify("幫我解釋一下這是什麼？")

    assert intent.kind == "question"
    assert intent.long_running is False
    assert intent.should_seed_active_task is False
    assert intent.done_criteria == ("the answer is clear and directly addresses the question",)
    assert intent.expects_code_change is False
    assert intent.expects_verification is False


def test_task_intent_classifier_records_media_upload_without_text():
    intent = TaskIntentService().classify("", images=["image-data"])

    assert intent.kind == "media_upload"
    assert intent.objective == "Save attached media for later use"
    assert intent.should_seed_active_task is False


def test_task_intent_debug_diagnosis_does_not_require_code_change():
    intent = TaskIntentService().classify("Please investigate why the build is failing.")

    assert intent.kind == "debug"
    assert intent.expects_code_change is False
    assert intent.expects_verification is False


def test_task_intent_respects_no_edit_planning_constraint():
    intent = TaskIntentService().classify(
        "Plan a refactor for src/opensprite/tools/web_research.py, but do not edit files."
    )

    assert intent.kind == "refactor"
    assert intent.expects_code_change is False


def test_task_intent_keeps_translation_as_direct_question():
    intent = TaskIntentService().classify("請把這句翻成英文：今天我想測試 CLI 對話流程。")

    assert intent.kind == "question"
    assert intent.should_seed_active_task is False
    assert intent.expects_code_change is False
    assert intent.expects_verification is False


def test_task_intent_keeps_chinese_translation_with_test_word_as_direct_question():
    intent = TaskIntentService().classify(
        "\u8acb\u628a\u9019\u53e5\u7ffb\u6210\u82f1\u6587\uff1a\u4eca\u5929\u6211\u60f3\u6e2c\u8a66 CLI \u5c0d\u8a71\u6d41\u7a0b\u3002"
    )

    assert intent.kind == "question"
    assert intent.should_seed_active_task is False
    assert intent.expects_code_change is False
    assert intent.expects_verification is False


def test_task_intent_treats_testing_notes_as_discussion_not_verification():
    intent = TaskIntentService().classify(
        "\u6211\u5011\u8981\u6e2c\u8a66\u4e00\u6bb5\u591a\u8f2a\u5c0d\u8a71\u3002\u4e0d\u8981\u8b80\u6a94\u6848\u3001\u4e0d\u8981\u4e0a\u7db2\uff0c\u8acb\u5217\u51fa\u6e2c\u8a66\u91cd\u9ede\u3002"
    )

    assert intent.expects_verification is False
    assert intent.verification_hint is None


def test_task_intent_treats_flow_risk_summary_as_discussion_not_verification():
    intent = TaskIntentService().classify(
        "\u73fe\u5728\u8acb\u7528\u76ee\u524d session \u7684\u8108\u7d61\uff0c\u7e3d\u7d50\u525b\u525b\u6e2c\u8a66\u5230\u76ee\u524d\u70ba\u6b62\u53ef\u80fd\u66b4\u9732\u51fa\u7684\u5169\u7a2e\u6d41\u7a0b\u98a8\u96aa\uff1b\u4e0d\u8981\u4e0a\u7db2\u3002"
    )

    assert intent.expects_verification is False


def test_task_intent_keeps_calculation_as_direct_question():
    intent = TaskIntentService().classify("請計算 17 * 23 + 19，最後只輸出答案。")

    assert intent.kind == "question"
    assert intent.should_seed_active_task is False


def test_task_intent_classifier_marks_chinese_extract_and_merge_request_as_task():
    intent = TaskIntentService().classify(
        "你把全部的prompt 都先抓出來 後 整合成一份 給我 有重疊部分 你看著處理"
    )

    assert intent.kind == "analysis"
    assert intent.should_seed_active_task is True
    assert intent.expects_code_change is False
