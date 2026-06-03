"""Shared runtime tool name constants."""

from __future__ import annotations


BATCH_TOOL_NAME = "batch"
READ_FILE_TOOL_NAME = "read_file"
LIST_DIR_TOOL_NAME = "list_dir"
GLOB_FILES_TOOL_NAME = "glob_files"
GREP_FILES_TOOL_NAME = "grep_files"
CODE_NAVIGATION_TOOL_NAME = "code_navigation"
APPLY_PATCH_TOOL_NAME = "apply_patch"
WRITE_FILE_TOOL_NAME = "write_file"
EDIT_FILE_TOOL_NAME = "edit_file"
EXEC_TOOL_NAME = "exec"
PROCESS_TOOL_NAME = "process"
WORKSPACE_DISCOVERY_TOOL_NAMES = frozenset(
    {
        READ_FILE_TOOL_NAME,
        LIST_DIR_TOOL_NAME,
        GLOB_FILES_TOOL_NAME,
        GREP_FILES_TOOL_NAME,
        CODE_NAVIGATION_TOOL_NAME,
    }
)
WORKSPACE_WRITE_TOOL_NAMES = frozenset(
    {
        APPLY_PATCH_TOOL_NAME,
        WRITE_FILE_TOOL_NAME,
        EDIT_FILE_TOOL_NAME,
    }
)
EXECUTION_TOOL_NAMES = frozenset({EXEC_TOOL_NAME, PROCESS_TOOL_NAME})
