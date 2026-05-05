from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import PipelineRun, Report, Vendor, VendorRun
from app.pipeline.runner import create_pipeline_run
from app.templating import templates

router = APIRouter()


@router.post("/runs")
async def create_run(session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    vendor_ids = list(
        (
            await session.execute(
                select(Vendor.id).where(Vendor.removed_at.is_(None)).order_by(Vendor.added_at)
            )
        ).scalars().all()
    )
    if not vendor_ids:
        raise HTTPException(status_code=400, detail="No vendors to analyze — add some first.")

    await create_pipeline_run(session, vendor_ids)
    # Land users on /reports — that's where the value materializes. The run
    # progress page (/runs/{id}) still exists for debugging via direct URL.
    return RedirectResponse(url="/reports", status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def run_page(
    run_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    run = await session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=404)
    rows = await _load_progress_rows(session, run_id)
    return templates.TemplateResponse(request, "run.html", {"run": run, "rows": rows})


@router.get("/runs/{run_id}/fragment", response_class=HTMLResponse)
async def run_fragment(
    run_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    run = await session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=404)
    rows = await _load_progress_rows(session, run_id)
    return templates.TemplateResponse(
        request, "_run_progress.html", {"run": run, "rows": rows}
    )


async def _load_progress_rows(
    session: AsyncSession, run_id: int
) -> list[tuple[VendorRun, Vendor, Report | None]]:
    """Per-vendor row: (vendor_run, vendor, latest_report_for_vendor_or_None)."""
    pairs = (
        await session.execute(
            select(VendorRun, Vendor)
            .join(Vendor, VendorRun.vendor_id == Vendor.id)
            .where(VendorRun.run_id == run_id)
            .order_by(Vendor.domain)
        )
    ).all()

    enriched: list[tuple[VendorRun, Vendor, Report | None]] = []
    for vr, v in pairs:
        report = (
            await session.execute(
                select(Report)
                .where(Report.vendor_id == v.id)
                .order_by(Report.generated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        enriched.append((vr, v, report))
    return enriched
