from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import SignalExtraction, Vendor
from app.pipeline.insights import (
    GRID_CERTS,
    InsightsBundle,
    compute_compliance_grid,
    compute_concentration,
    compute_freshness,
)
from app.templating import templates

router = APIRouter()


@router.get("/insights", response_class=HTMLResponse)
async def insights_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Cross-vendor view: concentration, compliance gaps, privacy freshness."""
    rows = (
        await session.execute(
            select(Vendor.domain, SignalExtraction.signal_type, SignalExtraction.payload)
            .join(SignalExtraction, SignalExtraction.vendor_id == Vendor.id)
            .where(Vendor.removed_at.is_(None))
        )
    ).all()

    by_signal: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    domains_with_data: set[str] = set()
    for domain, signal_type, payload in rows:
        by_signal[signal_type].append((domain, payload or {}))
        domains_with_data.add(domain)

    bundle = InsightsBundle(
        concentration=compute_concentration(by_signal.get("subprocessors", [])),
        compliance=compute_compliance_grid(by_signal.get("security", [])),
        freshness=compute_freshness(by_signal.get("privacy", [])),
        vendors_analyzed=len(domains_with_data),
    )

    return templates.TemplateResponse(
        request,
        "insights.html",
        {"bundle": bundle, "grid_certs": GRID_CERTS},
    )
