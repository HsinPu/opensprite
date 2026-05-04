# USER.md Template - Session User Context

This bootstrap file is a template for new session `USER.md` files.
It is not the active user profile for existing sessions.
Active session profiles live under `~/.opensprite/workspace/sessions/{channel}/{external_chat_id}/USER.md`.

Each session `USER.md` stores durable user-focused context for that session: stable preferences, recurring constraints, and background that improve future collaboration in the same session.
It should not contain assistant-wide rules, task progress, one-off tasks, project decisions, or private secrets.

## Purpose

Use this file for information that should remain useful across multiple turns in this session, especially when it helps OpenSprite collaborate more effectively without asking the same thing again.

## What Belongs Here

- stable preferences
- recurring work context
- long-lived constraints
- repeated habits or goals likely to matter again
- durable language or formatting preferences

## What Does Not Belong Here

- one-off requests
- transient task details
- task progress or project decisions that belong in MEMORY.md
- temporary session noise
- secrets, passwords, API keys, or access tokens
- assistant operating rules that belong in bootstrap files

## Response language

This section is maintained by OpenSprite.
Use a short durable preference when one is clear, such as `- Traditional Chinese (Taiwan)` or `- English`.
Use `- not set` when response language should follow the user's current message.

<!-- OPENSPRITE:RESPONSE_LANGUAGE:START -->
- not set
<!-- OPENSPRITE:RESPONSE_LANGUAGE:END -->

## Auto-managed User Context

This section is maintained by OpenSprite and should stay concise, factual, and durable.
Store only user-focused details that are stable enough to help future turns in this same session.

<!-- OPENSPRITE:USER_PROFILE:START -->
### Communication Preferences
- No learned communication preferences yet.

### Work Context
- No learned work context yet.

### Stable Constraints
- No learned stable constraints yet.
<!-- OPENSPRITE:USER_PROFILE:END -->
