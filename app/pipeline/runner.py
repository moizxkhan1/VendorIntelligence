"""Pipeline orchestrator — runs the six-stage pipeline for a single vendor and
the per-run fan-out across all vendors.

Stages, in order:
    discovery -> ranking -> selection -> fetching -> extraction -> analysis

Failures within a stage are caught at the vendor level — one vendor's bad
fetch doesn't crash the entire run. The VendorRun row carries `current_stage`
so the UI can show live progress; `status` flips to "done" or "failed" once
the vendor finishes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.llm import LLMProvider
from app.models import DiscoveredUrl, ScrapedPage, Vendor, VendorRun
from app.pipeline.analysis import analyze_vendor, persist_report
from app.pipeline.discovery import discover_urls, persist_discovery
from app.pipeline.extraction import extract_for_vendor, persist_extraction
from app.pipeline.fetcher import fetch_page, persist_page
from app.pipeline.ranking import rank_vendor, top_urls_per_signal
from app.pipeline.selection import HEURISTIC_TOP_K, select_for_extraction

MAX_PARALLEL_VENDORS = 4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run_pipeline(run_id: int, llm: LLMProvider) -> None:
    """Process every VendorRun under a PipelineRun, with bounded concurrency."""
    async with SessionLocal() as s:
        vendor_run_ids: list[int] = list(
            (
                await s.execute(
                    select(VendorRun.id).where(VendorRun.run_id == run_id)
                )
            ).scalars().all()
        )

    sem = asyncio.Semaphore(MAX_PARALLEL_VENDORS)

    async def gated(vrid: int) -> None:
        async with sem:
            await run_for_vendor(vrid, llm)

    await asyncio.gather(*(gated(vrid) for vrid in vendor_run_ids))


async def run_for_vendor(vendor_run_id: int, llm: LLMProvider) -> None:
    """Run the six-stage pipeline for one vendor; tolerate per-vendor failure."""
    async with SessionLocal() as session:
        vr = await session.get(VendorRun, vendor_run_id)
        if vr is None:
            return
        vendor = await session.get(Vendor, vr.vendor_id)
        if vendor is None:
            vr.status = "failed"
            vr.error = "vendor missing"
            vr.finished_at = _utcnow()
            await session.commit()
            return

        vr.status = "running"
        vr.started_at = _utcnow()
        await session.commit()

        async def mark_stage(name: str) -> None:
            vr.current_stage = name
            await session.commit()

        try:
            await mark_stage("discovery")
            disco = await discover_urls(vendor.domain, vendor.aliases or [])
            await persist_discovery(session, vendor, disco)

            await mark_stage("ranking")
            await rank_vendor(session, vendor)

            await mark_stage("selection")
            candidates = await top_urls_per_signal(session, vendor, n=HEURISTIC_TOP_K)
            selected = await select_for_extraction(llm, vendor, candidates)
            if not selected:
                vr.status = "failed"
                vr.error = "no usable URLs surfaced for any signal"
                vr.finished_at = _utcnow()
                await session.commit()
                return

            await mark_stage("fetching")
            unique_urls: list[DiscoveredUrl] = []
            seen: set[str] = set()
            for sig_urls in selected.values():
                for du in sig_urls:
                    if du.url not in seen:
                        seen.add(du.url)
                        unique_urls.append(du)

            fetch_results = await asyncio.gather(
                *(fetch_page(du.url) for du in unique_urls)
            )
            scraped: list[ScrapedPage] = []
            used_browser_any = False
            for du, fr in zip(unique_urls, fetch_results):
                used_browser_any = used_browser_any or fr.used_browser
                scraped.append(await persist_page(session, du, fr))

            await mark_stage("extraction")
            intel = await extract_for_vendor(llm, vendor, scraped)
            await persist_extraction(session, vendor, intel, [p.url for p in scraped])

            await mark_stage("analysis")
            risk = await analyze_vendor(session, vendor)
            await persist_report(session, vendor, risk)

            vr.status = "done"
            vr.current_stage = "done"
            vr.used_browser = used_browser_any
            vr.finished_at = _utcnow()
            await session.commit()

        except Exception as e:
            vr.status = "failed"
            vr.error = f"{type(e).__name__}: {str(e)[:480]}"
            vr.finished_at = _utcnow()
            await session.commit()


async def create_pipeline_run(session: AsyncSession, vendor_ids: list[int]) -> int:
    """Insert a PipelineRun + one VendorRun per vendor; return the run id."""
    from app.models import PipelineRun  # local import to keep module headers short

    run = PipelineRun(status="pending", trigger="manual")
    session.add(run)
    await session.commit()
    await session.refresh(run)
    for vid in vendor_ids:
        session.add(VendorRun(run_id=run.id, vendor_id=vid, status="pending"))
    await session.commit()
    return run.id
