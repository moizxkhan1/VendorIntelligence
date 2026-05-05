from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Vendor
from app.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def home(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    rows = (
        await session.execute(
            select(Vendor)
            .where(Vendor.removed_at.is_(None))
            .order_by(Vendor.added_at.desc())
        )
    ).scalars().all()
    return templates.TemplateResponse(request, "home.html", {"vendors": rows})
