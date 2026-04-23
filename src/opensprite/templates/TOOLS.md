# TOOLS.md - Tool Contract

Tool schemas and arguments are provided automatically by function calling.
This file defines when to use tools, how to choose between them, and what constraints matter.

## General Rules

- Prefer using a tool over guessing when the tool can answer the question directly.
- Prefer the narrowest tool that fits the job.
- Before non-trivial tool use, check whether a relevant skill exists. If it does, read the skill first so you can follow its workflow before using other tools.
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
  - Supports managed background execution with `background=true` or `yield_ms=<milliseconds>`; background runs return a `session_id` for follow-up inspection via `process`.
  - Managed background notifications default to `notify_on_exit=true` and `notify_on_exit_empty_success=false`; quiet successful runs stay silent unless you opt in.
  - Dangerous commands and obvious destructive patterns are blocked.
  - If a command could still cause irreversible or external side effects, ask first.
  - **Stdout/stderr are piped** from the shell subprocess and returned in arrival order; stderr lines are prefixed with `[stderr]`. There is no interactive stdin (commands that wait for TTY input will stall or time out).
  - **Background `&` and shell wrappers** (`nohup`, `disown`, `setsid`) are **rejected** in `exec`: they fight piped stdout/stderr and hang or lose output. Use the managed `background=true` / `yield_ms` path instead.
  - Commands that **look like long-lived dev servers** (e.g. `uvicorn`, `vite`, `npm run dev`, `python -m http.server`, …) are rejected unless they are clearly `--help` / `--version` style invocations or you explicitly request managed background execution.
- `process`
  - Inspects managed background exec sessions.
  - `action="list"` shows known sessions with current runtime.
  - `action="inspect"` returns metadata only for one `session_id` (status, timing, output flags, exit info).
  - `action="poll"` returns newly captured output and current status/timing for one `session_id`.
  - `action="log"` returns the full captured output plus timing for one `session_id`.
  - `action="kill"` terminates one managed background session and returns its final status/output tail.
  - `action="clear"` removes exited sessions; with `session_id` it clears one exited session, otherwise it clears all exited sessions.
  - Exited sessions are pruned automatically, keeping only the most recent managed history in memory.

## External Knowledge Tools

- `web_search`
  - Use to discover external sources, URLs, or recent information.
  - Prefer this when you need candidate sources before reading one in detail.
  - If the topic may already have been researched in the current chat, prefer `search_knowledge` first.
- `web_fetch`
  - Use to retrieve and extract readable content from a specific URL.
  - Prefer this after `web_search` or when the user already gave a URL.
  - If the current chat may already contain fetched content for the same page, prefer `search_knowledge` with `source_type="web_fetch"` before fetching again.

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

## Audio Tool

- `transcribe_audio`
  - Use when the current user turn includes voice or audio and the main need is the spoken content.
  - Prefer this for voice messages, recorded notes, or audio clips that should be turned into text before reasoning further.
  - Use `audio_index` when multiple audio clips are attached.
  - Add `language` only when a language hint is actually helpful.
  - If the user asks for summary, action items, or analysis of the audio, transcribe it first and then continue from the transcript.

## Video Tool

- `analyze_video`
  - Use when the current user turn includes a video and the task depends on understanding what happens in the clip.
  - Prefer this for screen recordings, short visual demonstrations, or clips where motion or sequence matters.
  - Always provide a clear `instruction` describing what to inspect in the video.
  - Use `video_index` when multiple video clips are attached.
  - If the main need is spoken content rather than visual sequence, prefer `transcribe_audio` first when an audio path is available.

## Skill Tool

- `read_skill`
  - Use when a specialized skill is relevant to the task.
  - Read the skill before following its workflow or conventions.
  - When a relevant skill exists for coding, editing, research, or multi-step work, load it before using other non-trivial tools.
- `configure_skill`
  - Use when the user wants to add, update, inspect, or remove skills (each skill is a folder with `SKILL.md`).
  - Prefer `configure_skill` instead of asking the user to create or edit skill files manually.
  - Before designing a **new** skill, load `**read_skill`** with `**skill-creator-design`** (bundled guide: metadata, English frontmatter, triggers in `description`, lean body, progressive disclosure, optional `scripts/` / `references/` / `assets/`).
  - Do **not** edit bundled skills on disk: they live under `~/.opensprite/skills/<skill_id>/` (synced from the package), same folder layout as session `skills/<skill_id>/`. Mutable copies live **only** under the current session workspace `skills/`. `configure_skill` always targets that path. `write_file` / `edit_file` refuse any path under `~/.opensprite/skills/`.
  - Use `action=add` to create a new skill only (fails if it already exists); use `action=upsert` to create or overwrite.
  - Enforced by the tool: `skill_name` must be lowercase ASCII, letter-first, hyphen-separated segments; `description` and `body` must meet minimum lengths; `description` also needs enough English words, substantive vocabulary (not only glue words), and must not be repetitive padding (see tool schema).
  **When to persist or refine skills without the user asking**
  - Skills are **procedural memory**: reusable *how-to* for a class of tasks. Use `**save_memory`** for stable facts, preferences, or chat-specific reminders; use `**configure_skill`** when the workflow itself should be saved for later.
  - After you finish a **non-trivial** task (several tool calls, backtracking, or the user corrected your approach), consider whether the successful approach is **reusable**. If yes, `**action=upsert`** a skill (or refine an existing one: `action=get` then `upsert` with merged content). Skip if nothing generalizable was learned.
  - Do not create noisy or one-off skills; one clear skill beats many thin ones.

## Subagent prompt tool

- `configure_subagent`
  - Use when the user wants to add, update, inspect, or remove **subagent** prompts for **this chat session**.
  - **Writes** (`add`, `upsert`, `remove`) go only under the current session workspace: `subagent_prompts/<subagent_id>.md` (same session-relative idea as `skills/`). Prefer this tool instead of `write_file` / `edit_file` for those paths.
  - `**list`** and `**get`** return **merged** ids and content: if a session file exists for an id, it overrides the copy under `~/.opensprite/subagent_prompts/<id>.md`; otherwise defaults come from app home (seeded from the package on first sync).
  - Before designing a **new** subagent prompt, load `**read_skill`** with `**agent-creator-design`** (Role, Task, Constraints, Output; metadata and naming rules).
  - Use `action=add` only for a **brand-new** id: no file in this session's `subagent_prompts/` yet **and** no prompt for that id under `~/.opensprite/subagent_prompts/`. If app home already has that id, use `action=upsert` to create or replace the **session** file.
  - `action=remove` deletes **only** the session workspace file; it does **not** remove files under `~/.opensprite/subagent_prompts/`.
  - Same strict `subagent_id` format as `skill_name` for `configure_skill`; `description` and `body` follow the same minimum quality rules as `configure_skill` (see tool schema).
  **When you judge a new subagent is worth adding**
  - You may decide on your own that a **new, reusable** subagent id would help (e.g. a standing code-review or security-review expert) when repeated work would benefit from a dedicated prompt and `delegate` with existing ids is not enough.
  - Before `action=add` for a **new** `subagent_id`, **ask the user for confirmation in plain text** unless they already explicitly asked you to create that subagent (same id or same role). One short message: what the subagent would do, the proposed `subagent_id`, and a clear yes/no (or equivalent). **Do not** call `configure_subagent` with `add` until they agree.
  - After they agree (or they already asked): load `read_skill` with `agent-creator-design`, then call `configure_subagent` with `action=add`, `**user_confirmed: true`**, plus `description` and `body`. For a **brand-new** id (no prompt in app home yet for that id), the tool **rejects** `add` without `user_confirmed: true` (hard gate).
  - If they decline or ignore, do not create the file; continue with a one-off answer or `delegate` to an existing `prompt_type` instead.

## MCP Configuration

When the user wants to add, update, inspect, or remove MCP servers, prefer using `configure_mcp` instead of telling the user to edit config files manually.

- Use `configure_mcp` first for MCP setup or changes.
- After changing MCP settings, prefer reloading MCP in the current session when the tool supports it.
- Only ask the user to edit MCP JSON files directly if the tool cannot express the required change.

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
  - Prefer this before repeating `web_search` or `web_fetch` on the same topic inside the same chat.
  - Use `provider`, `extractor`, `status`, `content_type`, and `truncated` filters to narrow large result sets when needed.

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
  - `prompt_type` must be an **existing** subagent id in the **merged** list (session `subagent_prompts/` overrides `~/.opensprite/subagent_prompts/` when both exist). To add or change prompts for this chat, use `**configure_subagent`** (session files) and `**read_skill`** with `**agent-creator-design**` before authoring a new prompt; then call `delegate` with the id.
  - If no suitable id exists yet, follow the **configure_subagent** rules above: propose a new id, **ask the user before `add`**, then create and delegate.

## Scope Boundaries

- `**USER.md**` is stored at this **chat session workspace root**: `~/.opensprite/workspace/chats/<channel>/<chat_id>/USER.md` — the same folder as `**skills/`** and `**subagent_prompts/`**. It holds durable user-focused context for **this session** (preferences, constraints, and OpenSprite-maintained blocks).
- `**MEMORY.md`** lives under `**~/.opensprite/memory/<chat_id>/MEMORY.md`** and holds durable narrative/chat continuity (what you usually think of as long-lived chat memory).
- Prefer `configure_skill` and `configure_subagent` when defining those trees rather than ad-hoc edits elsewhere.
- `search_history` and `search_knowledge` are for on-demand retrieval, not always-on memory.
