"""Runtime helpers specific to exec-style shell command execution."""

import asyncio
import os
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CapturedOutputChunk:
    """One captured stdout/stderr chunk in arrival order."""

    stream_name: str
    data: bytes


def _append_stderr_text(text: str, result: list[str], needs_prefix: bool) -> bool:
    if not text:
        return needs_prefix

    for line in text.splitlines(keepends=True):
        if needs_prefix:
            result.append("[stderr] ")
        result.append(line)
        needs_prefix = line.endswith(("\n", "\r"))

    return needs_prefix


def format_captured_output(
    output_chunks: list[CapturedOutputChunk],
    *,
    max_chars: int | None = 3000,
    empty_placeholder: str = "(no output)",
) -> str:
    """Render captured stdout/stderr chunks into user-facing text."""
    result: list[str] = []
    stderr_needs_prefix = True

    for chunk in output_chunks:
        text = chunk.data.decode("utf-8", errors="replace")
        if chunk.stream_name == "stderr":
            stderr_needs_prefix = _append_stderr_text(text, result, stderr_needs_prefix)
        else:
            result.append(text)

    output = "".join(result).strip()
    if not output:
        output = empty_placeholder

    if max_chars is not None and len(output) > max_chars:
        output = output[:max_chars] + f"\n\n... (truncated, total {len(output)} chars)"

    return output


def _process_creation_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


async def _read_process_stream(
    stream: asyncio.StreamReader | None,
    *,
    stream_name: str,
    output_chunks: list[CapturedOutputChunk],
) -> None:
    if stream is None:
        return

    while True:
        chunk = await stream.read(4096)
        if not chunk:
            return
        output_chunks.append(CapturedOutputChunk(stream_name=stream_name, data=chunk))


async def start_shell_process(
    command: str,
    *,
    cwd: str | None,
    output_chunks: list[CapturedOutputChunk],
) -> tuple[asyncio.subprocess.Process, list[asyncio.Task[None]]]:
    """Start a shell command with piped stdout/stderr collection."""
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        cwd=cwd,
        **_process_creation_kwargs(),
    )
    read_tasks = [
        asyncio.create_task(
            _read_process_stream(process.stdout, stream_name="stdout", output_chunks=output_chunks)
        ),
        asyncio.create_task(
            _read_process_stream(process.stderr, stream_name="stderr", output_chunks=output_chunks)
        ),
    ]
    return process, read_tasks


async def drain_process_output(read_tasks: list[asyncio.Task[None]], *, timeout: float) -> bool:
    """Wait for output readers to finish, cancelling them on timeout."""
    try:
        await asyncio.wait_for(asyncio.gather(*read_tasks), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        for task in read_tasks:
            task.cancel()
        await asyncio.gather(*read_tasks, return_exceptions=True)
        return False
