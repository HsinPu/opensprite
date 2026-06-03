"""Shared tool group routing constants for agent contracts."""

from __future__ import annotations

from .harness_profile import (
    CODE_CHANGE_TASK_TYPE,
    GENERIC_TASK_TYPE,
    HISTORY_RETRIEVAL_TASK_TYPE,
    HISTORY_RETRIEVAL_TOOL_GROUP,
    MEDIA_EXTRACTION_TASK_TYPE,
    OPERATIONS_TASK_TYPE,
    VERIFICATION_TOOL_GROUP,
    WORKSPACE_READ_TASK_TYPE,
    WORKSPACE_READ_TOOL_GROUP,
    WORKSPACE_WRITE_TOOL_GROUP,
)
from .history_retrieval_policy import HISTORY_SEARCH_TOOL_NAME
from .verification_policy import VERIFICATION_TOOL_NAME
from .web_source_policy import WEB_RESEARCH_TASK_TYPE, WEB_RESEARCH_TOOL_GROUP, WEB_SOURCE_ARTIFACT_TOOLS


TOOL_GROUPS: dict[str, frozenset[str]] = {
    "image_text": frozenset({"ocr_image", "analyze_image"}),
    "image_understanding": frozenset({"analyze_image"}),
    "audio_text": frozenset({"transcribe_audio"}),
    "execution": frozenset({"exec", "process"}),
    "media": frozenset({"analyze_image", "ocr_image", "transcribe_audio", "analyze_video"}),
    "scheduling": frozenset({"cron"}),
    "video_understanding": frozenset({"analyze_video"}),
    WEB_RESEARCH_TOOL_GROUP: WEB_SOURCE_ARTIFACT_TOOLS,
    HISTORY_RETRIEVAL_TOOL_GROUP: frozenset({HISTORY_SEARCH_TOOL_NAME, "list_run_file_changes"}),
    WORKSPACE_READ_TOOL_GROUP: frozenset(
        {
            "read_file",
            "glob_files",
            "grep_files",
            "code_navigation",
            "list_run_file_changes",
            "preview_run_file_change_revert",
        }
    ),
    WORKSPACE_WRITE_TOOL_GROUP: frozenset({"apply_patch", "write_file", "edit_file"}),
    VERIFICATION_TOOL_GROUP: frozenset({VERIFICATION_TOOL_NAME, "exec"}),
}

TOOL_GROUP_BY_TOOL_NAME: dict[str, str] = {
    tool_name: tool_group
    for tool_group, tool_names in TOOL_GROUPS.items()
    for tool_name in tool_names
}

TASK_TYPE_BY_TOOL_GROUP: dict[str, str] = {
    "audio_text": MEDIA_EXTRACTION_TASK_TYPE,
    "execution": OPERATIONS_TASK_TYPE,
    HISTORY_RETRIEVAL_TOOL_GROUP: HISTORY_RETRIEVAL_TASK_TYPE,
    "image_text": MEDIA_EXTRACTION_TASK_TYPE,
    "image_understanding": MEDIA_EXTRACTION_TASK_TYPE,
    "media": MEDIA_EXTRACTION_TASK_TYPE,
    "scheduling": OPERATIONS_TASK_TYPE,
    VERIFICATION_TOOL_GROUP: GENERIC_TASK_TYPE,
    "video_understanding": MEDIA_EXTRACTION_TASK_TYPE,
    WEB_RESEARCH_TOOL_GROUP: WEB_RESEARCH_TASK_TYPE,
    WORKSPACE_READ_TOOL_GROUP: WORKSPACE_READ_TASK_TYPE,
    WORKSPACE_WRITE_TOOL_GROUP: CODE_CHANGE_TASK_TYPE,
}
