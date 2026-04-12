# TOOLS.md - Tool Contract

Tool schemas and arguments are provided automatically by function calling.
This file defines when to use tools, how to choose between them, and what constraints matter.

## General Rules

- Prefer using a tool over guessing when the tool can answer the question directly.
- Prefer the narrowest tool that fits the job.
- Stay within the active workspace unless the user clearly asks for something external.
- Some tools are optional and only appear when enabled at runtime.

## Workspace Tools

- `list_dir`
  - Use to inspect directories before reading or editing.
  - Good first step when the file location is unclear.

- `read_file`
  - Use to inspect existing files inside the workspace.
  - Prefer this before editing unless the exact target text is already known.

- `write_file`
  - Use to create a new file or fully replace a file's contents.
  - Do not use this for small in-place edits when `edit_file` is safer.

- `edit_file`
  - Use for targeted replacements in existing files.
  - It requires an exact unique `old_text` match.
  - If the target text is ambiguous, read the file first and use a more specific replacement.

## Command Tool

- `exec`
  - Runs shell commands inside the active workspace.
  - Default timeout: 60 seconds.
  - Use for verification, project inspection, builds, tests, and other command-line tasks.
  - Dangerous commands and obvious destructive patterns are blocked.
  - If a command could still cause irreversible or external side effects, ask first.

## External Knowledge Tools

- `web_search`
  - Use to discover external sources, URLs, or recent information.
  - Prefer this when you need candidate sources before reading one in detail.

- `web_fetch`
  - Use to retrieve and extract readable content from a specific URL.
  - Prefer this after `web_search` or when the user already gave a URL.

## Image Tool

- `analyze_image`
  - Use when the current user turn includes one or more images and the task requires visual understanding.
  - Use it for screenshots, photos, diagrams, visual bug reports, UI review, or image-based questions.
  - Always provide a clear `instruction` that says what to inspect and what kind of answer is needed.
  - If multiple images are attached, use `image_index` to choose the correct one.
  - If the user only wants a normal text answer and the image is irrelevant, do not call it.
  - If a specialized image-reading skill is relevant, read that skill first and then call `analyze_image` with a prompt shaped by the skill.

- `ocr_image`
  - Use when the main goal is to extract visible text from an image rather than general visual understanding.
  - Prefer this for screenshots with error messages, receipts, documents, forms, labels, or photographed text.
  - Use `image_index` when multiple images are attached.
  - Add an optional `instruction` only when you need OCR to focus on a specific section or formatting need.

## Skill Tool

- `read_skill`
  - Use when a specialized skill is relevant to the task.
  - Read the skill before following its workflow or conventions.

## Memory And Retrieval Tools

- `save_memory`
  - Use to update durable chat-specific memory in `memory/{chat_id}/MEMORY.md`.
  - Save only information likely to matter again later.
  - Do not save secrets or one-turn noise.

- `search_history`
  - Use to retrieve prior conversation details from the current chat.
  - Search before claiming you do not remember an earlier discussion.

- `search_knowledge`
  - Use to retrieve previously stored `web_search` and `web_fetch` results from the current chat.
  - Prefer this when the user refers to earlier research rather than current local files.

## Scheduling Tool

- `cron`
  - Use when the user wants work to happen later, on a recurring interval, or on a calendar schedule.
  - Use `at` for one-time future tasks.
  - Use `every_seconds` for fixed recurring intervals.
  - Use `cron_expr` for calendar-style schedules such as daily or weekday runs.
  - If the requested time or timezone is ambiguous, ask one short clarifying question before creating the job.
  - Prefer `deliver=true` when the user expects a reminder or a pushed result in chat.
  - Prefer `deliver=false` when the job is only meant to update local state or prepare work silently.
  - Before creating a schedule, make sure the task message is explicit enough that a future agent run can execute it without missing context.
  - Use `list` before claiming there are no scheduled jobs.
  - Use `remove` when the user asks to cancel or delete a previously scheduled job.

## Delegation

- `delegate`
  - Use to hand off a bounded task to a specialized subagent.
  - Prefer this for focused subproblems that benefit from a dedicated prompt.
  - Do not delegate trivial work that can be completed directly.

## Scope Boundaries

- `USER.md` stores durable user-wide context.
- `memory/{chat_id}/MEMORY.md` stores durable chat-specific context.
- `search_history` and `search_knowledge` are for on-demand retrieval, not always-on memory.
