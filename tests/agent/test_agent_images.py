import asyncio

from agent_test_helpers import make_agent_loop
from opensprite.agent.execution import ExecutionResult
from opensprite.agent.task_intent import TaskIntentService


def _make_media_agent(tmp_path):
    return make_agent_loop(tmp_path / "workspace", include_images=True)


def _capture_first_prompt(agent):
    captured = {}

    async def fake_execute(
        log_id,
        chat_messages,
        *,
        allow_tools,
        tool_result_session_id=None,
        tool_registry=None,
        on_tool_before_execute=None,
        on_tool_after_execute=None,
        on_llm_status=None,
        on_response_delta=None,
        on_tool_input_delta=None,
        on_reasoning_delta=None,
        refresh_system_prompt=None,
        max_tool_iterations=None,
        should_cancel=None,
        work_state_summary="",
    ):
        captured["content"] = chat_messages[0].content
        return ExecutionResult(content="ok", executed_tool_calls=0, used_configure_skill=False)

    agent._execute_messages = fake_execute  # type: ignore[method-assign]
    return captured


def test_call_llm_replaces_direct_image_payload_with_tool_hint(tmp_path):
    agent = _make_media_agent(tmp_path)
    captured = _capture_first_prompt(agent)

    result = asyncio.run(
        agent.call_llm(
            "telegram:user-a",
            current_message="What is in this image?",
            channel="telegram",
            user_images=["img-a"],
        )
    )

    assert result.content == "ok"
    assert (
        "User attached 1 image(s). Use analyze_image or ocr_image only if the user's text asks for visual understanding or text extraction."
        in captured["content"]
    )


def test_call_llm_includes_task_contract_guidance_for_media_task(tmp_path):
    agent = _make_media_agent(tmp_path)
    captured = _capture_first_prompt(agent)
    intent = TaskIntentService().classify(
        "Please inspect this image and summarize it.",
        images=["img-a"],
    )

    result = asyncio.run(
        agent.call_llm(
            "telegram:user-a",
            current_message="Please inspect this image and summarize it.",
            channel="telegram",
            user_images=["img-a"],
            user_image_files=["images/a.jpg"],
            task_intent=intent,
        )
    )

    assert result.content == "ok"
    content = captured["content"]
    assert "## Runtime Task Contract" in content
    assert "Task type: media_extraction" in content
    assert "image:images/a.jpg" in content
    assert "Required evidence" in content
    assert "tool_group=image_text" in content
    assert "Final answer acceptance criteria" in content
    assert "substantive final answer" in content


def test_call_llm_adds_audio_tool_hint_to_prompt(tmp_path):
    agent = _make_media_agent(tmp_path)
    captured = _capture_first_prompt(agent)
    audio_token = agent._current_audios.set(["aud-a"])
    try:
        result = asyncio.run(
            agent.call_llm(
                "telegram:user-a",
                current_message="What did this person say?",
                channel="telegram",
                user_images=None,
            )
        )
    finally:
        agent._current_audios.reset(audio_token)

    assert result.content == "ok"
    assert "User attached 1 audio clip(s). Use transcribe_audio only if the user's text asks for spoken content." in captured[
        "content"
    ]


def test_call_llm_adds_video_tool_hint_to_prompt(tmp_path):
    agent = _make_media_agent(tmp_path)
    captured = _capture_first_prompt(agent)
    video_token = agent._current_videos.set(["vid-a"])
    try:
        result = asyncio.run(
            agent.call_llm(
                "telegram:user-a",
                current_message="What happens in this clip?",
                channel="telegram",
                user_images=None,
            )
        )
    finally:
        agent._current_videos.reset(video_token)

    assert result.content == "ok"
    assert "User attached 1 video clip(s). Use analyze_video only if the user's text asks for video understanding." in captured[
        "content"
    ]
