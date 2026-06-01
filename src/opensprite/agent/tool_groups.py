"""Shared tool group routing constants for agent contracts."""

from __future__ import annotations


TOOL_GROUPS: dict[str, frozenset[str]] = {
    "image_text": frozenset({"ocr_image", "analyze_image"}),
    "image_understanding": frozenset({"analyze_image"}),
    "audio_text": frozenset({"transcribe_audio"}),
    "execution": frozenset({"exec", "process"}),
    "media": frozenset({"analyze_image", "ocr_image", "transcribe_audio", "analyze_video"}),
    "scheduling": frozenset({"cron"}),
    "video_understanding": frozenset({"analyze_video"}),
    "web_research": frozenset({"web_search", "web_fetch", "web_research", "browser_navigate", "browser_snapshot"}),
    "history_retrieval": frozenset({"search_history", "list_run_file_changes"}),
    "workspace_read": frozenset(
        {
            "read_file",
            "glob_files",
            "grep_files",
            "code_navigation",
            "list_run_file_changes",
            "preview_run_file_change_revert",
        }
    ),
    "workspace_write": frozenset({"apply_patch", "write_file", "edit_file"}),
    "verification": frozenset({"verify", "exec"}),
}

TOOL_GROUP_BY_TOOL_NAME: dict[str, str] = {
    tool_name: tool_group
    for tool_group, tool_names in TOOL_GROUPS.items()
    for tool_name in tool_names
}

TASK_TYPE_BY_TOOL_GROUP: dict[str, str] = {
    "audio_text": "media_extraction",
    "execution": "operations",
    "history_retrieval": "history_retrieval",
    "image_text": "media_extraction",
    "image_understanding": "media_extraction",
    "media": "media_extraction",
    "scheduling": "operations",
    "verification": "task",
    "video_understanding": "media_extraction",
    "web_research": "web_research",
    "workspace_read": "workspace_read",
    "workspace_write": "code_change",
}
