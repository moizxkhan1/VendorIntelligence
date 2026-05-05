from datetime import datetime, timezone

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
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
    vendor_runs: Mapped[list["VendorRun"]] = relationship(
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
    scraped_pages: Mapped[list["ScrapedPage"]] = relationship(
        back_populates="discovered_url", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("vendor_id", "url", name="uq_discovered_url_vendor_url"),
    )

    def __repr__(self) -> str:
        return f"DiscoveredUrl(id={self.id}, url={self.url!r}, source={self.source!r})"


class ScrapedPage(Base):
    __tablename__ = "scraped_page"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    discovered_url_id: Mapped[int | None] = mapped_column(
        ForeignKey("discovered_url.id", ondelete="CASCADE"), index=True, nullable=True
    )
    url: Mapped[str] = mapped_column(String(2048), index=True)
    final_url: Mapped[str] = mapped_column(String(2048))
    http_status: Mapped[int] = mapped_column(Integer)
    content_html: Mapped[str] = mapped_column(Text, default="")
    content_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    used_browser: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)

    discovered_url: Mapped["DiscoveredUrl | None"] = relationship(back_populates="scraped_pages")

    def __repr__(self) -> str:
        return (
            f"ScrapedPage(id={self.id}, url={self.url!r}, "
            f"http_status={self.http_status}, used_browser={self.used_browser})"
        )


class SignalExtraction(Base):
    """One row per (vendor, signal_type) — the structured LLM output.

    `payload` is the JSON form of the matching Pydantic signal model
    (SecurityPosture, SubProcessors, etc.). `source_urls` records which
    pages were in the LLM context for traceability.
    """

    __tablename__ = "signal_extraction"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendor.id", ondelete="CASCADE"), index=True
    )
    signal_type: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    source_urls: Mapped[list[str]] = mapped_column(JSON, default=list)
    extracted_at: Mapped[datetime] = mapped_column(default=utcnow)

    def __repr__(self) -> str:
        return f"SignalExtraction(vendor_id={self.vendor_id}, signal_type={self.signal_type!r})"


class Report(Base):
    """Composite renewal-risk report for a vendor.

    History is preserved (no replace-on-write) so the diff-vs-last-run
    feature has data to compare against. The latest report is the row
    with the highest `generated_at`.
    """

    __tablename__ = "report"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendor.id", ondelete="CASCADE"), index=True
    )
    risk_score: Mapped[int] = mapped_column(Integer)
    risk_band: Mapped[str] = mapped_column(String(16), index=True)
    components: Mapped[list] = mapped_column(JSON)   # [ScoreComponent]
    red_flags: Mapped[list] = mapped_column(JSON)    # [RedFlag]
    summary_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)

    def __repr__(self) -> str:
        return f"Report(vendor_id={self.vendor_id}, score={self.risk_score}, band={self.risk_band!r})"


class PipelineRun(Base):
    """A single end-to-end analysis pass over a set of vendors."""

    __tablename__ = "pipeline_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="pending")
    # pending | running | done | failed
    trigger: Mapped[str] = mapped_column(String(16), default="manual")
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    vendor_runs: Mapped[list["VendorRun"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"PipelineRun(id={self.id}, status={self.status!r})"


class VendorRun(Base):
    """One vendor's slot in a PipelineRun. current_stage is updated as it
    progresses so the UI can show live status."""

    __tablename__ = "vendor_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("pipeline_run.id", ondelete="CASCADE"), index=True
    )
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendor.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending | running | done | failed
    current_stage: Mapped[str] = mapped_column(String(16), default="queued")
    # queued | discovery | ranking | selection | fetching | extraction | analysis | done
    used_browser: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    run: Mapped[PipelineRun] = relationship(back_populates="vendor_runs")
    vendor: Mapped[Vendor] = relationship(back_populates="vendor_runs")

    def __repr__(self) -> str:
        return f"VendorRun(id={self.id}, vendor_id={self.vendor_id}, status={self.status!r}, stage={self.current_stage!r})"
