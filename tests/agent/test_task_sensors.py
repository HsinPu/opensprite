from opensprite.agent.completion_gate import CompletionGateResult
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.execution_support.artifacts import TaskArtifact
from opensprite.agent.task.capabilities import evaluate_task_sensors
from opensprite.tools.evidence import ToolEvidence


def test_conversation_sensors_pass_for_plain_final_answer():
    sensors = evaluate_task_sensors(
        task_type="conversation",
        execution_result=ExecutionResult(content="done"),
        completion_result=CompletionGateResult(status="complete", reason="answered"),
    )

    assert [(sensor.sensor_id, sensor.status) for sensor in sensors] == [
        ("chat.no_unexpected_tools", "pass"),
        ("completion.final_answer", "pass"),
    ]


def test_web_research_sensors_require_sources_and_live_evidence():
    sensors = evaluate_task_sensors(
        task_type="web_research",
        execution_result=ExecutionResult(
            content="done",
            tool_evidence=(ToolEvidence(name="web_research"),),
            task_artifacts=(TaskArtifact(kind="web_source", source_tool="web_research"),),
        ),
        completion_result=CompletionGateResult(status="complete", reason="cited sources"),
    )

    assert [(sensor.sensor_id, sensor.status) for sensor in sensors] == [
        ("research.source_coverage", "pass"),
        ("research.freshness", "pass"),
        ("completion.source_grounding", "pass"),
    ]


def test_code_change_sensors_record_missing_change_and_verification():
    sensors = evaluate_task_sensors(
        task_type="code_change",
        execution_result=ExecutionResult(content="done", verification_attempted=True, verification_passed=False),
        completion_result=CompletionGateResult(status="incomplete", reason="needs code changes"),
    )

    assert [(sensor.sensor_id, sensor.status) for sensor in sensors] == [
        ("coding.file_change", "fail"),
        ("coding.verification", "warn"),
        ("completion.change_summary", "fail"),
    ]


def test_media_extraction_sensor_uses_shared_media_artifact_policy():
    sensors = evaluate_task_sensors(
        task_type="media_extraction",
        execution_result=ExecutionResult(
            content="done",
            task_artifacts=(TaskArtifact(kind="audio_transcript", source_tool="transcribe_audio"),),
        ),
        completion_result=CompletionGateResult(status="complete", reason="summarized media"),
    )

    assert [(sensor.sensor_id, sensor.status) for sensor in sensors] == [
        ("media.artifact", "pass"),
        ("completion.media_summary", "pass"),
    ]
