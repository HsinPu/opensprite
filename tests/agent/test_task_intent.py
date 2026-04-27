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
    assert intent.constraints == ("Keep the public API stable.",)


def test_task_intent_classifier_keeps_question_out_of_active_task_seed():
    intent = TaskIntentService().classify("幫我解釋一下這是什麼？")

    assert intent.kind == "question"
    assert intent.long_running is False
    assert intent.should_seed_active_task is False
    assert intent.done_criteria == ("the answer is clear and directly addresses the question",)


def test_task_intent_classifier_records_media_upload_without_text():
    intent = TaskIntentService().classify("", images=["image-data"])

    assert intent.kind == "media_upload"
    assert intent.objective == "Save attached media for later use"
    assert intent.should_seed_active_task is False
