import asyncio
import json

from opensprite.cron.manager import CronManager
from opensprite.cron.service import CronService
from opensprite.cron.types import CronJob, CronSchedule


def test_cron_service_persists_session_and_jobs(tmp_path):
    store_path = tmp_path / "cron" / "jobs.json"
    service = CronService(store_path, session_id="telegram:user-a")

    service.add_job(
        name="reminder",
        schedule=CronSchedule(kind="every", every_ms=60_000),
        message="ping",
        deliver=True,
        channel="telegram",
        external_chat_id="user-a",
    )

    data = json.loads(store_path.read_text(encoding="utf-8"))

    assert data["sessionId"] == "telegram:user-a"
    assert data["jobs"][0]["payload"]["message"] == "ping"
    assert data["jobs"][0]["payload"]["externalChatId"] == "user-a"


def test_cron_service_runs_one_shot_job_and_removes_it(tmp_path):
    calls = []

    async def on_job(job: CronJob):
        calls.append(job.id)
        return "ok"

    async def scenario():
        service = CronService(tmp_path / "cron" / "jobs.json", session_id="telegram:user-a", on_job=on_job)
        job = service.add_job(
            name="once",
            schedule=CronSchedule(kind="at", at_ms=1),
            message="once",
            delete_after_run=True,
        )
        await service.run_job(job.id)
        return service.list_jobs(include_disabled=True)

    jobs = asyncio.run(scenario())

    assert calls != []
    assert jobs == []


def test_cron_manager_keeps_jobs_in_separate_session_files(tmp_path):
    async def on_job(session_id: str, job: CronJob):
        return f"{session_id}:{job.id}"

    async def scenario():
        manager = CronManager(workspace_root=tmp_path / "workspace", on_job=on_job)
        service_a = await manager.get_or_create_service("telegram:user-a")
        service_b = await manager.get_or_create_service("telegram:user-b")
        service_a.add_job("job-a", CronSchedule(kind="every", every_ms=1_000), "A")
        service_b.add_job("job-b", CronSchedule(kind="every", every_ms=1_000), "B")
        await manager.stop()
        return service_a.store_path, service_b.store_path

    path_a, path_b = asyncio.run(scenario())

    assert path_a != path_b
    assert path_a.exists()
    assert path_b.exists()
    assert json.loads(path_a.read_text(encoding="utf-8"))["sessionId"] == "telegram:user-a"
    assert json.loads(path_b.read_text(encoding="utf-8"))["sessionId"] == "telegram:user-b"
