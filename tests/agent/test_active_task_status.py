from opensprite.agent.active_task_status import (
    BLOCKED_ACTIVE_TASK_DEFAULT_OPEN_QUESTION,
    WAITING_USER_ACTIVE_TASK_DEFAULT_OPEN_QUESTION,
    active_task_status,
    clear_open_questions,
    has_current_active_task,
    is_current_active_task_status,
    is_current_or_done_active_task_status,
    normalize_open_questions,
)


def test_active_task_status_parses_rendered_status_line():
    block = "- Goal: Demo\n- Status: waiting_user\n- Current step: inspect"

    assert active_task_status(block) == "waiting_user"
    assert has_current_active_task(block) is True


def test_active_task_status_defaults_to_inactive():
    assert active_task_status("- Goal: Demo") == "inactive"
    assert has_current_active_task("- Status: done") is False


def test_active_task_status_helpers_normalize_stored_status_values():
    assert is_current_active_task_status(" WAITING_USER ") is True
    assert is_current_active_task_status("done") is False
    assert is_current_or_done_active_task_status("done") is True
    assert is_current_or_done_active_task_status("inactive") is False


def test_active_task_default_open_questions_are_stable():
    assert WAITING_USER_ACTIVE_TASK_DEFAULT_OPEN_QUESTION == "need user input"
    assert BLOCKED_ACTIVE_TASK_DEFAULT_OPEN_QUESTION == "blocked"


def test_clear_open_questions_returns_sentinel_list():
    assert clear_open_questions() == ["none"]


def test_normalize_open_questions_trims_and_preserves_questions():
    assert normalize_open_questions([" first ", "", "second"]) == ["first", "second"]


def test_normalize_open_questions_accepts_clear_sentinel():
    assert normalize_open_questions(["blocked", "NONE"]) == ["none"]
