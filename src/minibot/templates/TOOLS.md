# Tool Usage Notes

Tool signatures are provided automatically via function calling.
This file documents non-obvious constraints and usage patterns.

## Files

- **read_file**: Read file content (limited to workspace)
- **write_file**: Write/create files (limited to workspace)
- **edit_file**: Edit files by replacing exact text (limited to workspace)
- **list_dir**: List directory contents

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

- **web_search**: Search the web (requires Brave API key)
- **web_fetch**: Fetch web page content
