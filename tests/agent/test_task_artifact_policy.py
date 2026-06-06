from opensprite.agent.task_artifact import TASK_ARTIFACTS_NOT_PRODUCED_REASON


def test_task_artifacts_not_produced_reason_is_stable():
    assert TASK_ARTIFACTS_NOT_PRODUCED_REASON == "required task artifacts were not produced"
