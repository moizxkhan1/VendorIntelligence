import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.pipeline.browser import shutdown_browser
from app.routes import pages, runs, vendors
from app.workers.runner import recover_stale_runs, worker_loop

logging.basicConfig(level=settings.log_level.upper())


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await recover_stale_runs()

    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(worker_loop(stop_event), name="vendor-intel-worker")

    try:
        yield
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(worker_task, timeout=10)
        except asyncio.TimeoutError:
            worker_task.cancel()
        await shutdown_browser()


app = FastAPI(
    title="vendor-intel",
    description="Vendor intelligence reports for SaaS procurement.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(pages.router)
app.include_router(vendors.router)
app.include_router(runs.router)
