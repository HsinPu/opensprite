---
name: memory
description: Persistent memory with auto-consolidation. Important facts are automatically saved and loaded across sessions.
always: true
---

# Memory

## Structure

- `memory/{chat_id}/MEMORY.md` — Long-term facts (preferences, project context). Automatically loaded into context.

## When to Update MEMORY.md

Use `edit_file` or `write_file` to immediately save:

**User Preferences**
- Timezone, language, communication style
- Tool/editor preferences ("uses VS Code", "prefers TypeScript")
- Response format preferences

**Project Context**
- Tech stack (frameworks, libraries, versions)
- Architecture decisions
- API endpoints, database schemas
- Current goals and blockers

**Important Facts**
- Names, relationships, organizations
- Deadlines, schedules, recurring events
- Personal details the user shares
