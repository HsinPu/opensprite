import asyncio

from opensprite.cron.manager import CronManager
from opensprite.cron.types import CronJob
from opensprite.tools.cron import CronTool


def test_cron_tool_add_list_and_remove(tmp_path):
    async def on_job(session_chat_id: str, job: CronJob):
        return "ok"

    async def scenario():
        manager = CronManager(workspace_root=tmp_path / "workspace", on_job=on_job)
        tool = CronTool(manager, get_chat_id=lambda: "telegram:user-a")

        created = await tool.execute(
            action="add",
            name="check-weather",
            message="Check weather and report back",
            every_seconds=300,
            deliver=True,
        )
        listed = await tool.execute(action="list")
        service = await manager.get_or_create_service("telegram:user-a")
        job_id = service.list_jobs(include_disabled=True)[0].id
        removed = await tool.execute(action="remove", job_id=job_id)
        listed_after = await tool.execute(action="list")
        await manager.stop()
        return created, listed, removed, listed_after

    created, listed, removed, listed_after = asyncio.run(scenario())

    assert "Created job 'check-weather'" in created
    assert "Scheduled jobs:" in listed
    assert "check-weather" in listed
    assert "every 5m" in listed
    assert removed.startswith("Removed job ")
    assert listed_after == "No scheduled jobs."
