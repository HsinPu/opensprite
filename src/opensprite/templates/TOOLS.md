# TOOLS.md - Tool Contract

Tool schemas and arguments are provided automatically by function calling.
This file defines when to use tools, how to choose between them, and what tool-specific constraints matter.
Keep high-level workflow in `AGENTS.md`; keep concrete tool usage rules here.

## General Tool Rules

- Prefer using a tool over guessing when the tool can answer the question directly.
- Prefer the narrowest tool that fits the job.
- Before non-trivial tool use, check whether a relevant skill exists and read it first when appropriate.
- Stay within the active workspace unless the user clearly asks for something external.
- Some tools are optional and may not appear at runtime; use only the tools that are actually available.

## Workspace Tools

- `list_dir`
  - Use to inspect directories before reading, creating, or editing files.
  - Prefer this when the target path is not yet certain.

- `read_file`
  - Use to inspect existing file contents inside the workspace.
  - Prefer this before editing unless the exact target text is already known.

- `write_file`
  - Use to create a new file or replace a file completely.
  - Do not use this for a small in-place edit when `edit_file` is safer.

- `edit_file`
  - Use for targeted replacements in existing files.
  - It requires an exact and unique `old_text` match.
  - If the replacement target is ambiguous, read the file first and use a more specific edit.

## Command Tools

- `exec`
  - Use for verification, project inspection, builds, tests, and other shell work.
  - Default timeout is 60 seconds unless configured otherwise.
  - Use managed background execution with `background=true` or `yield_ms=<milliseconds>` when the command is long-running and you need to inspect it later with `process`.
  - Dangerous commands and obvious destructive patterns are blocked, but you must still judge user intent and risk.
  - Ask before commands that may cause irreversible changes, data loss, network side effects, or costly external actions.
  - There is no interactive stdin. Commands that expect a TTY can stall or fail.
  - Do not use shell background wrappers such as `&`, `nohup`, `disown`, or `setsid`; use managed background execution instead.
  - Commands that look like long-lived dev servers are rejected unless clearly informational or intentionally managed as background runs.

- `process`
  - Use to inspect managed background `exec` sessions.
  - `action="list"` shows known sessions.
  - `action="inspect"` returns metadata for one session.
  - `action="poll"` returns newly captured output since the last read.
  - `action="log"` returns the full captured output.
  - `action="kill"` terminates a managed session.
  - `action="clear"` removes exited sessions from history.

## External Knowledge Tools

- `web_search`
  - Use when you need fresh external sources, candidate URLs, or current information.
  - Prefer this before `web_fetch` when you do not yet know which page to read.
  - If the topic may already have been researched in the current chat, prefer `search_knowledge` first.

- `web_fetch`
  - Use to retrieve readable content from a specific URL.
  - Prefer this after `web_search` or when the user already provided a URL.
  - If the current chat may already contain fetched content for the same page, prefer `search_knowledge` before fetching again unless freshness matters.

## Media Tools

- `analyze_image`
  - Use when the current user turn includes images and the task requires visual understanding.
  - Good fits: screenshots, UI review, diagrams, visual bug reports, or image-based questions.
  - Always provide a clear `instruction` describing what to inspect and what answer is needed.
  - If multiple images are attached, use `image_index` to select the right one.
  - If the image is irrelevant to the user's request, do not call it.

- `ocr_image`
  - Use when the main need is extracting visible text rather than general visual understanding.
  - Good fits: screenshots with errors, documents, forms, receipts, labels, or photographed text.
  - Use `image_index` when multiple images are attached.
  - Add `instruction` only when OCR should focus on a specific section or output shape.

- `transcribe_audio`
  - Use when the current user turn includes voice or audio and the main need is the spoken content.
  - Good fits: voice messages, recorded notes, spoken instructions, or clips that should become text before further reasoning.
  - Use `audio_index` when multiple audio clips are attached.
  - Add `language` only when a language hint is genuinely useful.

- `analyze_video`
  - Use when the current user turn includes a video and the task depends on understanding events or visual sequence.
  - Good fits: screen recordings, demonstrations, motion-dependent issues, or short clips where sequence matters.
  - Always provide a clear `instruction` describing what to inspect.
  - Use `video_index` when multiple video clips are attached.
  - If the main need is spoken content rather than visual sequence, prefer `transcribe_audio` first when applicable.

- `send_media`
  - Use when the user asks you to send, return, or resend an image, voice message, audio file, or video.
  - Provide `payload` when you already have a data URL, URL, or platform file id to send.
  - Omit `payload` and set `media_index` to resend media attached to the current user turn.
  - Choose `kind="voice"` for Telegram-style voice messages and `kind="audio"` for regular audio files.

## Skill Tools

- `read_skill`
  - Use when a specialized skill is relevant to the task.
  - Read the skill before following its workflow or conventions.

- `configure_skill`
  - Use when the user wants to add, update, inspect, or remove reusable skills.
  - Prefer `configure_skill` over manual editing of skill files.
  - Before designing a new skill, read `skill-creator-design` first.
  - Bundled skills under `~/.opensprite/skills/` are read-only defaults; mutable session copies belong under the session workspace `skills/` tree.
  - Use `action="add"` only for a brand-new skill; use `action="upsert"` to create or overwrite.
  - Respect tool-enforced naming and quality rules for `skill_name`, `description`, and `body`.
  - Consider writing or refining a skill only when the workflow is clearly reusable, non-trivial, and worth preserving.

## Subagent Prompt Tool

- `configure_subagent`
  - Use when the user wants to add, update, inspect, or remove subagent prompts for this chat session.
  - Session writes go under the current workspace `subagent_prompts/` tree.
  - `list` and `get` reflect merged results: session prompts override app-home defaults when both exist.
  - Before designing a new subagent prompt, read `agent-creator-design` first.
  - Use `action="add"` only for a truly brand-new id; use `action="upsert"` when updating or overriding an existing id for this session.
  - `action="remove"` deletes only the session override, not the app-home default file.
  - Respect tool-enforced naming and quality rules for `subagent_id`, `description`, and `body`.
  - Before creating a brand-new reusable subagent id, ask the user for confirmation unless they already explicitly asked for that exact expert.

## MCP Configuration Tool

- `configure_mcp`
  - Use when the user wants to add, update, inspect, or remove MCP server configuration.
  - Use `configure_mcp` first for MCP setup or changes.
  - prefer using `configure_mcp` instead of telling the user to edit config files manually.
  - Reload MCP in the current session after config changes when the tool supports it.
  - Ask the user to edit config files directly only if the tool cannot express the required change.

## Memory And Retrieval Tools

- `save_memory`
  - Use to update durable chat-specific continuity in `memory/{chat_id}/MEMORY.md`.
  - Save only information likely to matter again later.
  - Do not save secrets, temporary noise, or one-turn details.

- `search_history`
  - Use to retrieve prior conversation details from the current chat.
  - Search before claiming you do not remember earlier discussion in this chat.

- `search_knowledge`
  - Use to retrieve previously stored `web_search` and `web_fetch` results from the current chat.
  - Prefer this when the user refers to earlier research instead of current local files.
  - Prefer this before repeating `web_search` or `web_fetch` on the same topic in the same chat.
  - Use filters such as `provider`, `extractor`, `status`, `content_type`, and `truncated` when narrowing large result sets.

## Scheduling Tool

- `cron`
  - Use when the user wants work to happen later, repeatedly, or on a calendar schedule.
  - Use `at` for one-time future tasks.
  - Use `every_seconds` for fixed intervals.
  - Use `cron_expr` for calendar-style schedules.
  - If time or timezone is ambiguous, ask one short clarifying question before creating the job.
  - Prefer `deliver=true` when the user expects a reminder or a pushed result in chat.
  - Prefer `deliver=false` when the job should run silently.
  - Before creating a schedule, make sure the task message is explicit enough that a future run can execute it without missing context.
  - Use `list` before claiming there are no scheduled jobs.
  - Use `remove` when the user asks to cancel or delete an existing schedule.

## Delegation Tool

- `delegate`
  - Use to hand off a bounded subproblem to a specialized subagent.
  - Prefer this for focused work that benefits from a dedicated prompt.
  - Do not delegate trivial work that can be completed directly.
  - `prompt_type` must already exist in the merged subagent list.
  - If no suitable prompt exists, follow the `configure_subagent` rules above before delegating to a new reusable id.

## Scope Boundaries

- `USER.md` lives at the chat session workspace root and stores durable user-focused context for this session.
- `MEMORY.md` lives under `~/.opensprite/memory/` and stores durable chat continuity.
- Prefer `configure_skill` and `configure_subagent` for those trees rather than ad-hoc edits elsewhere.
- `search_history` and `search_knowledge` are retrieval tools, not always-on memory.
