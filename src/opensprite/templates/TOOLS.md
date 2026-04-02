# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## Files

- **read_file**: Read file content (limited to workspace)
- **write_file**: Write/create files (limited to workspace)
- **edit_file**: Edit files by replacing exact text (limited to workspace)
- **list_dir**: List directory contents

## Tool Use Policy

- Tool use is an internal execution detail unless the user explicitly asks about your process.
- If a tool can help you complete the task, use it and return the completed result instead of telling the user that you could use a tool.
- Do not announce internal delegation, tool selection, or worker handoff before doing the work.
- Do not attribute the final result to a subagent or internal worker unless the user explicitly asks about the process.

## System

- **exec**: Execute shell commands
  - Timeout: 60 seconds
  - Limited to workspace directory
  - Dangerous commands are blocked:
    - `rm -rf`, `del /f`, `rmdir /s`
    - `format`, `mkfs`, `diskpart`
    - `dd` (direct disk access)
    - Writing to `/dev/sd*`
    - `shutdown`, `reboot`, `poweroff`
    - Fork bombs

## Web

- **web_search**: Search the web using the configured provider
- **web_fetch**: Fetch web page content

## Delegation

- **delegate**: Use this internally when a focused writing or execution subtask should be completed directly.
  - Treat delegated work as internal execution, not user-facing narration.
  - Do not tell the user you are handing work to a subagent unless they explicitly ask how the work is being done.
  - Prefer returning the completed draft, analysis, or result in your final answer.
  - Do not prefix the result with lines like "this was written by a subagent".

## Search

- **search_history**: Search saved conversation history for the current chat only
  - Use this when the user asks what was discussed before, references an earlier decision, or expects you to remember details that are not in the current visible context.
  - Search before saying you cannot remember prior chat details.
- **search_knowledge**: Search saved `web_search` and `web_fetch` results for the current chat only
  - Use this when the user asks about previously researched external information, URLs, findings, or summaries from earlier web lookups.
- **memory vs search**
  - `memory/{chat_id}/MEMORY.md` is always loaded and should hold durable facts, preferences, and decisions.
  - `search_history` and `search_knowledge` are on-demand tools for details that should not be injected into every prompt.
