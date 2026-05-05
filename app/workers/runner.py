"""Background worker — polls pipeline_run for pending rows and processes them.

Runs in-process under FastAPI's lifespan. SQLite-backed: there's no Redis
or external queue. Single worker; SQLite is single-writer anyway, so adding
parallel workers would add contention without benefit.

On app restart, `recover_stale_runs()` marks any rows still in-flight from
the previous process as failed. Resuming partial runs would require
per-stage idempotency that we don't have yet.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.db import SessionLocal
from app.llm import get_llm
from app.models import PipelineRun, VendorRun
from app.pipeline.runner import run_pipeline

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 2.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def claim_pending_run() -> int | None:
    """Atomically transition the oldest pending run to 'running' and return its id."""
    async with SessionLocal() as s:
        run_id = (
            await s.execute(
                select(PipelineRun.id)
                .where(PipelineRun.status == "pending")
                .order_by(PipelineRun.id)
                .limit(1)
            )
        ).scalar_one_or_none()

        if run_id is None:
            return None

        result = await s.execute(
            update(PipelineRun)
            .where(PipelineRun.id == run_id, PipelineRun.status == "pending")
            .values(status="running", started_at=_utcnow())
        )
        await s.commit()
        return run_id if result.rowcount > 0 else None


async def recover_stale_runs() -> None:
    """Mark in-flight runs from a prior process as failed."""
    async with SessionLocal() as s:
        await s.execute(
            update(PipelineRun)
            .where(PipelineRun.status == "running")
            .values(status="failed", finished_at=_utcnow())
        )
        await s.execute(
            update(VendorRun)
            .where(VendorRun.status.in_(("running", "pending")))
            .values(status="failed", finished_at=_utcnow(), error="app restarted mid-run")
        )
        await s.commit()


async def _fail_run(run_id: int, reason: str) -> None:
    async with SessionLocal() as s:
        run = await s.get(PipelineRun, run_id)
        if run is not None:
            run.status = "failed"
            run.finished_at = _utcnow()
        await s.execute(
            update(VendorRun)
            .where(VendorRun.run_id == run_id, VendorRun.status.in_(("pending", "running")))
            .values(status="failed", finished_at=_utcnow(), error=reason)
        )
        await s.commit()


async def _mark_run_finished(run_id: int) -> None:
    async with SessionLocal() as s:
        run = await s.get(PipelineRun, run_id)
        if run is None:
            return
        run.status = "done"
        run.finished_at = _utcnow()
        await s.commit()


async def worker_loop(stop_event: asyncio.Event) -> None:
    """Pulls pending pipeline_runs and processes them serially."""
    log.info("vendor-intel worker started")
    while not stop_event.is_set():
        run_id = await claim_pending_run()

        if run_id is None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL_S)
                if stop_event.is_set():
                    break
            except asyncio.TimeoutError:
                pass
            continue

        try:
            llm = get_llm()
        except ValueError as e:
            await _fail_run(run_id, f"LLM not configured: {e}")
            continue

        try:
            await run_pipeline(run_id, llm)
            await _mark_run_finished(run_id)
        except Exception as e:
            log.exception("run %d crashed", run_id)
            await _fail_run(run_id, f"{type(e).__name__}: {str(e)[:480]}")

    log.info("vendor-intel worker stopped")
