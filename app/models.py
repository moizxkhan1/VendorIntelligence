from datetime import datetime, timezone

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Vendor(Base):
    __tablename__ = "vendor"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    added_at: Mapped[datetime] = mapped_column(default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    def __repr__(self) -> str:
        return f"Vendor(id={self.id}, domain={self.domain!r})"
