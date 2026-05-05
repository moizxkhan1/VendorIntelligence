from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Vendor
from app.schemas import VendorCreate, normalize_domain
from app.templating import templates

# Starter list provided in the assessment brief.
STARTER_VENDORS: tuple[str, ...] = (
    "omni.co", "retool.com", "claude.ai", "intercom.com", "adobe.com",
    "jetbrains.com", "coderabbit.ai", "avalara.com", "datadoghq.com", "notion.so",
    "linear.app", "gong.io", "ramp.com", "brex.com", "lattice.com",
    "carta.com", "rippling.com", "segment.com", "amplitude.com",
)


router = APIRouter(prefix="/vendors", tags=["vendors"])


async def _active_vendors(session: AsyncSession) -> list[Vendor]:
    result = await session.execute(
        select(Vendor).where(Vendor.removed_at.is_(None)).order_by(Vendor.added_at.desc())
    )
    return list(result.scalars().all())


async def _exists(session: AsyncSession, domain: str) -> bool:
    result = await session.execute(
        select(Vendor.id).where(Vendor.domain == domain, Vendor.removed_at.is_(None))
    )
    return result.scalar_one_or_none() is not None


@router.post("", response_class=HTMLResponse)
async def add_vendor(
    request: Request,
    domain: str = Form(...),
    display_name: str | None = Form(None),
    aliases: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    payload = VendorCreate(domain=domain, display_name=display_name, aliases=aliases)

    if await _exists(session, payload.domain):
        raise HTTPException(status_code=409, detail=f"Vendor {payload.domain!r} already exists")

    vendor = Vendor(
        domain=payload.domain,
        display_name=payload.display_name,
        aliases=payload.aliases,
    )
    session.add(vendor)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Vendor {payload.domain!r} already exists")
    await session.refresh(vendor)

    return templates.TemplateResponse(request, "_vendor_row.html", {"v": vendor})


@router.delete("/{vendor_id}")
async def remove_vendor(
    vendor_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    vendor = await session.get(Vendor, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")
    await session.delete(vendor)
    await session.commit()
    # Empty body — HTMX swaps the targeted <tr> with nothing, removing it.
    return Response(status_code=200)


@router.post("/bulk", response_class=HTMLResponse)
async def bulk_import(
    request: Request,
    domains: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    await _insert_many(session, domains.splitlines())
    return templates.TemplateResponse(
        request, "_vendor_list.html", {"vendors": await _active_vendors(session)}
    )


@router.post("/seed", response_class=HTMLResponse)
async def seed_starter_vendors(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    await _insert_many(session, STARTER_VENDORS)
    return templates.TemplateResponse(
        request, "_vendor_list.html", {"vendors": await _active_vendors(session)}
    )


async def _insert_many(session: AsyncSession, raw: list[str] | tuple[str, ...]) -> None:
    seen: set[str] = set()
    for line in raw:
        d = normalize_domain(line)
        if not d or d in seen:
            continue
        seen.add(d)
        if await _exists(session, d):
            continue
        session.add(Vendor(domain=d, aliases=[]))
    await session.commit()
