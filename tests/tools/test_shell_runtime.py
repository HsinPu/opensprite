import asyncio

from opensprite.tools import shell_runtime as shell_runtime_module
from opensprite.utils import processes as processes_module


class _FakeProcess:
    def __init__(self, *, pid: int = 123):
        self.pid = pid
        self.stdout = None
        self.stderr = None


def test_start_shell_process_uses_expected_stdio_and_session_kwargs(monkeypatch):
    shell_calls = []

    async def fake_create_subprocess_shell(*args, **kwargs):
        shell_calls.append((args, kwargs))
        return _FakeProcess(pid=321)

    monkeypatch.setattr(processes_module.os, "name", "posix", raising=False)
    monkeypatch.setattr(
        shell_runtime_module.asyncio,
        "create_subprocess_shell",
        fake_create_subprocess_shell,
    )

    async def run():
        output_chunks = []
        process, read_tasks = await shell_runtime_module.start_shell_process(
            "echo hi",
            cwd="/tmp/demo",
            output_chunks=output_chunks,
        )
        drained = await shell_runtime_module.drain_process_output(read_tasks, timeout=0.01)
        return process, drained, output_chunks

    process, drained, output_chunks = asyncio.run(run())

    assert process.pid == 321
    assert drained is True
    assert output_chunks == []
    assert shell_calls == [
        (
            ("echo hi",),
            {
                "stdout": shell_runtime_module.asyncio.subprocess.PIPE,
                "stderr": shell_runtime_module.asyncio.subprocess.PIPE,
                "stdin": shell_runtime_module.asyncio.subprocess.DEVNULL,
                "cwd": "/tmp/demo",
                "start_new_session": True,
            },
        )
    ]


def test_drain_process_output_cancels_tasks_on_timeout():
    async def sleeper():
        await asyncio.sleep(1)

    async def run():
        task = asyncio.create_task(sleeper())
        drained = await shell_runtime_module.drain_process_output([task], timeout=0.01)
        return drained, task.cancelled()

    drained, was_cancelled = asyncio.run(run())

    assert drained is False
    assert was_cancelled is True


def test_format_captured_output_preserves_order_and_prefixes_stderr():
    output = shell_runtime_module.format_captured_output(
        [
            shell_runtime_module.CapturedOutputChunk("stdout", b"out1\n"),
            shell_runtime_module.CapturedOutputChunk("stderr", b"err1\nerr2\n"),
            shell_runtime_module.CapturedOutputChunk("stdout", b"out2"),
        ]
    )

    assert output == "out1\n[stderr] err1\n[stderr] err2\nout2"


def test_format_captured_output_returns_placeholder_and_truncates():
    assert shell_runtime_module.format_captured_output([]) == "(no output)"

    long_text = "x" * 20
    output = shell_runtime_module.format_captured_output(
        [shell_runtime_module.CapturedOutputChunk("stdout", long_text.encode("utf-8"))],
        max_chars=10,
    )

    assert output.startswith("x" * 10)
    assert "truncated, total 20 chars" in output
