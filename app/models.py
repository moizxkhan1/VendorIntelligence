from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

    discovered_urls: Mapped[list["DiscoveredUrl"]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"Vendor(id={self.id}, domain={self.domain!r})"


class DiscoveredUrl(Base):
    __tablename__ = "discovered_url"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendor.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2048))
    source: Mapped[str] = mapped_column(String(32))                # 'sitemap' | 'subdomain'
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)  # set by ranker
    score: Mapped[float | None] = mapped_column(nullable=True)               # set by ranker
    discovered_at: Mapped[datetime] = mapped_column(default=utcnow)

    vendor: Mapped[Vendor] = relationship(back_populates="discovered_urls")

    __table_args__ = (
        UniqueConstraint("vendor_id", "url", name="uq_discovered_url_vendor_url"),
    )

    def __repr__(self) -> str:
        return f"DiscoveredUrl(id={self.id}, url={self.url!r}, source={self.source!r})"
