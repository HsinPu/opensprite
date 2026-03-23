---
name: coding
description: Coding best practices. Always read full context before making changes.
always: true
---

# Coding Guidelines

## Before Making Changes

**Read the full context:**
1. Read the entire file first, not just the relevant section
2. Check imports and dependencies at the top
3. Look for related files (sibling files, parent/child classes, interfaces)
4. Search for usages of the function/class you're modifying

**Understand the patterns:**
- Naming conventions used in the codebase
- Error handling patterns
- Async/sync patterns
- Type hints and annotations style

## When Editing

**Use `edit_file` for small changes:**
- Provide exact old string (including indentation)
- Ensure the replacement is minimal and focused

**Use `write_file` for new files or major refactors**

**Always verify:**
- The file compiles/passes type checking
- Existing tests still pass
- No breaking changes to public APIs

## Common Pitfalls

❌ **Don't:**
- Edit code without reading the full file
- Assume function behavior without checking callers
- Change interfaces without checking all implementations
- Ignore error handling patterns already in place

✅ **Do:**
- Read related files (imports, base classes, interfaces)
- Search for usages before renaming/refactoring
- Follow existing code style and patterns
- Add type hints to new code

## Tools Workflow

1. `read_file` — Read the target file
2. `list_dir` — Understand project structure
3. `read_file` — Read related files (imports, tests, interfaces)
4. `edit_file` / `write_file` — Make the change
5. `exec` — Run tests/linter to verify
