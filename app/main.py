from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="vendor-intel",
    description="Vendor intelligence reports for SaaS procurement.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
async def home() -> dict[str, str]:
    return {"app": "vendor-intel", "status": "scaffold"}
