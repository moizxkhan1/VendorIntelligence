from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db
from app.pipeline.browser import shutdown_browser
from app.routes import pages, vendors


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        yield
    finally:
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
