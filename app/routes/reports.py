from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Report, SignalExtraction, Vendor
from app.templating import templates

router = APIRouter()


@router.get("/reports", response_class=HTMLResponse)
async def reports_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Latest Report per vendor, sorted by risk score (riskiest first)."""
    vendors = (
        await session.execute(
            select(Vendor).where(Vendor.removed_at.is_(None)).order_by(Vendor.domain)
        )
    ).scalars().all()

    rows: list[tuple[Vendor, Report | None]] = []
    for v in vendors:
        latest = (
            await session.execute(
                select(Report)
                .where(Report.vendor_id == v.id)
                .order_by(Report.generated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        rows.append((v, latest))

    # Riskiest reports first; vendors with no report sink to the bottom
    rows.sort(key=lambda r: (-1 if r[1] is None else r[1].risk_score), reverse=True)

    return templates.TemplateResponse(request, "reports.html", {"rows": rows})


@router.get("/reports/{vendor_id}.json")
async def report_json(
    vendor_id: int,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Machine-readable export of the latest report + all extractions."""
    vendor = await session.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404)

    latest = (
        await session.execute(
            select(Report)
            .where(Report.vendor_id == vendor_id)
            .order_by(Report.generated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    extractions = (
        await session.execute(
            select(SignalExtraction).where(SignalExtraction.vendor_id == vendor_id)
        )
    ).scalars().all()

    return JSONResponse(
        {
            "vendor": {
                "domain": vendor.domain,
                "display_name": vendor.display_name,
                "aliases": vendor.aliases,
            },
            "report": (
                None
                if latest is None
                else {
                    "risk_score": latest.risk_score,
                    "risk_band": latest.risk_band,
                    "components": latest.components,
                    "red_flags": latest.red_flags,
                    "generated_at": latest.generated_at.isoformat(),
                }
            ),
            "signals": {
                e.signal_type: {
                    "payload": e.payload,
                    "source_urls": e.source_urls,
                    "extracted_at": e.extracted_at.isoformat(),
                }
                for e in extractions
            },
        }
    )


@router.get("/reports/{vendor_id}", response_class=HTMLResponse)
async def report_detail(
    vendor_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    vendor = await session.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404)

    latest = (
        await session.execute(
            select(Report)
            .where(Report.vendor_id == vendor_id)
            .order_by(Report.generated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    prior = (
        await session.execute(
            select(Report)
            .where(Report.vendor_id == vendor_id)
            .order_by(Report.generated_at.desc())
            .offset(1)
            .limit(1)
        )
    ).scalar_one_or_none()

    extractions = (
        await session.execute(
            select(SignalExtraction).where(SignalExtraction.vendor_id == vendor_id)
        )
    ).scalars().all()
    extractions_by_type = {e.signal_type: e for e in extractions}

    diff = _compute_diff(latest, prior) if (latest and prior) else None

    return templates.TemplateResponse(
        request,
        "report.html",
        {
            "vendor": vendor,
            "report": latest,
            "prior": prior,
            "diff": diff,
            "extractions": extractions_by_type,
        },
    )


def _compute_diff(current: Report, prior: Report) -> dict:
    """Compare two Reports — score, band, components, flags. Used in the detail page."""
    score_delta = current.risk_score - prior.risk_score

    prior_comp = {c["name"]: c for c in (prior.components or [])}
    cur_comp = {c["name"]: c for c in (current.components or [])}
    component_changes: list[dict] = []
    for name, c in cur_comp.items():
        if name not in prior_comp:
            component_changes.append({
                "kind": "added", "label": c["label"],
                "detail": f"+{c['contribution']}" if c["contribution"] >= 0 else str(c["contribution"]),
            })
        elif c["contribution"] != prior_comp[name]["contribution"]:
            component_changes.append({
                "kind": "changed", "label": c["label"],
                "detail": f"{prior_comp[name]['contribution']:+d} → {c['contribution']:+d}",
            })
    for name, c in prior_comp.items():
        if name not in cur_comp:
            component_changes.append({"kind": "removed", "label": c["label"], "detail": "no longer scored"})

    prior_flags = {f["code"]: f for f in (prior.red_flags or [])}
    cur_flags = {f["code"]: f for f in (current.red_flags or [])}
    flag_changes: list[dict] = []
    for code in cur_flags.keys() - prior_flags.keys():
        flag_changes.append({"kind": "added", "label": cur_flags[code]["label"]})
    for code in prior_flags.keys() - cur_flags.keys():
        flag_changes.append({"kind": "resolved", "label": prior_flags[code]["label"]})

    return {
        "prior_generated_at": prior.generated_at,
        "score_delta": score_delta,
        "band_changed": current.risk_band != prior.risk_band,
        "prior_band": prior.risk_band,
        "component_changes": component_changes,
        "flag_changes": flag_changes,
    }
