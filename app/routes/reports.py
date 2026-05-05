from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import DiscoveredUrl, Report, ScrapedPage, SignalExtraction, Vendor, VendorRun
from app.templating import templates

router = APIRouter()

SIGNALS = ("security", "privacy", "subprocessors", "pricing", "ownership", "operating_health")


@router.get("/reports", response_class=HTMLResponse)
async def reports_list(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows, has_in_progress = await _load_reports_with_status(session)
    return templates.TemplateResponse(
        request,
        "reports.html",
        {"rows": rows, "has_in_progress": has_in_progress},
    )


@router.get("/reports/fragment", response_class=HTMLResponse)
async def reports_fragment(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows, has_in_progress = await _load_reports_with_status(session)
    return templates.TemplateResponse(
        request,
        "_reports_table.html",
        {"rows": rows, "has_in_progress": has_in_progress},
    )


@router.get("/reports/{vendor_id}.json")
async def report_json(
    vendor_id: int,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
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

    # Per-signal status: tells the template *why* a card is empty so we don't
    # show a mute "no data" pill. Three states surface to the user:
    #   found      — extraction has data
    #   missing    — pages were analyzed but this signal wasn't on them
    #   no-pages   — couldn't analyze the vendor's pages at all (rare, top-level)
    pages = (
        await session.execute(
            select(ScrapedPage)
            .join(DiscoveredUrl, ScrapedPage.discovered_url_id == DiscoveredUrl.id)
            .where(DiscoveredUrl.vendor_id == vendor_id)
        )
    ).scalars().all()
    successful_pages = [p for p in pages if p.error is None and 200 <= p.http_status < 400]
    failed_pages = [p for p in pages if p.error is not None or p.http_status >= 400]

    signal_status: dict[str, dict] = {}
    for sig in SIGNALS:
        if sig in extractions_by_type:
            signal_status[sig] = {"state": "found"}
            continue
        if not successful_pages:
            signal_status[sig] = {
                "state": "no-pages",
                "reason": "Could not analyze this vendor's pages."
                + (f" {len(failed_pages)} fetch failure(s)." if failed_pages else ""),
            }
        else:
            reason = "Not present on the pages we analyzed."
            if failed_pages:
                reason += f" ({len(failed_pages)} other page(s) failed to fetch.)"
            signal_status[sig] = {"state": "missing", "reason": reason}

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
            "signal_status": signal_status,
        },
    )


async def _load_reports_with_status(
    session: AsyncSession,
) -> tuple[list[tuple[Vendor, Report | None, VendorRun | None, VendorRun | None]], bool]:
    """For each active vendor: (vendor, latest_report, in_flight_run, last_finished_run)."""
    vendors = (
        await session.execute(
            select(Vendor).where(Vendor.removed_at.is_(None)).order_by(Vendor.domain)
        )
    ).scalars().all()

    out: list[tuple[Vendor, Report | None, VendorRun | None, VendorRun | None]] = []
    has_in_progress = False
    for v in vendors:
        report = (
            await session.execute(
                select(Report)
                .where(Report.vendor_id == v.id)
                .order_by(Report.generated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        in_flight = (
            await session.execute(
                select(VendorRun)
                .where(
                    VendorRun.vendor_id == v.id,
                    VendorRun.status.in_(("pending", "running")),
                )
                .order_by(VendorRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        last_finished = (
            await session.execute(
                select(VendorRun)
                .where(
                    VendorRun.vendor_id == v.id,
                    VendorRun.status.in_(("done", "failed")),
                )
                .order_by(VendorRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if in_flight is not None:
            has_in_progress = True
        out.append((v, report, in_flight, last_finished))

    # Sort: in-progress first, then failed-only, then by risk desc, then unanalyzed last.
    def sort_key(row: tuple) -> tuple:
        v, report, in_flight, last_finished = row
        if in_flight is not None:
            return (0, v.domain)
        if report is None:
            if last_finished is not None and last_finished.status == "failed":
                return (1, v.domain)
            return (3, v.domain)
        return (2, -report.risk_score, v.domain)

    out.sort(key=sort_key)
    return out, has_in_progress


def _compute_diff(current: Report, prior: Report) -> dict:
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
