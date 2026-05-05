"""Stage 5 — Risk scoring + report assembly.

Pure scoring functions per signal that produce attributed `ScoreComponent`s,
plus a set of red-flag detectors. The orchestrator hydrates the persisted
signal_extraction rows back into Pydantic models, runs the scorers, and
writes a Report.

Scoring is anchored: a vendor with zero data lands around 30 (low-amber).
Documented certifications pull the score down (good); missing them or
recent breaches push it up. Each component carries a `rationale` and an
`evidence_url` so a finance reviewer can audit one line at a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Report, SignalExtraction, Vendor
from app.schemas import (
    CertificationType,
    FlagSeverity,
    OperatingHealth,
    Ownership,
    OwnershipStatus,
    PricingModel,
    PricingSignal,
    PrivacyPolicy,
    RedFlag,
    RiskBand,
    RiskScore,
    ScoreComponent,
    SecurityPosture,
    SubProcessors,
)

# Score = ANCHOR + sum(contributions). Tuned so that a fully-clean vendor
# lands ~10-15 (green) and a vendor with no data + a recent breach lands ~85.
ANCHOR = 30
GREEN_MAX = 24
AMBER_MAX = 50


# --- per-signal scorers ------------------------------------------------------


def score_security(
    security: SecurityPosture | None, evidence: str | None
) -> ScoreComponent | None:
    if security is None:
        return None  # no data — skip; data-availability handled by caller

    if not security.certifications:
        return ScoreComponent(
            name="security_certs",
            label="Security certifications",
            contribution=25,
            rationale="No certifications visible on the vendor's pages",
            evidence_url=evidence,
        )

    types = {c.type for c in security.certifications}
    contribution = 0
    parts: list[str] = []

    if CertificationType.SOC2_T2 in types:
        contribution -= 10
        parts.append("SOC 2 Type II")
    elif CertificationType.SOC2_T1 in types:
        contribution -= 5
        parts.append("SOC 2 Type I")

    if CertificationType.ISO27001 in types:
        contribution -= 5
        parts.append("ISO 27001")
    if CertificationType.HIPAA in types:
        contribution -= 3
        parts.append("HIPAA")
    if {CertificationType.FEDRAMP_MODERATE, CertificationType.FEDRAMP_HIGH} & types:
        contribution -= 3
        parts.append("FedRAMP")
    if CertificationType.PCI_DSS in types:
        contribution -= 2
        parts.append("PCI DSS")

    contribution = max(-20, contribution)
    rationale = (
        "Has " + ", ".join(parts) if parts
        else "Has certifications but none of SOC 2 / ISO 27001 / HIPAA / FedRAMP / PCI"
    )
    return ScoreComponent(
        name="security_certs",
        label="Security certifications",
        contribution=contribution,
        rationale=rationale,
        evidence_url=evidence,
    )


def score_privacy_freshness(
    privacy: PrivacyPolicy | None, evidence: str | None, *, today: date | None = None
) -> ScoreComponent | None:
    if privacy is None or privacy.last_updated is None:
        return None
    today = today or date.today()
    months = (today.year - privacy.last_updated.year) * 12 + (today.month - privacy.last_updated.month)

    if months > 24:
        contribution, rationale = 10, f"Privacy policy last updated {months} months ago — neglect signal"
    elif months > 12:
        contribution, rationale = 5, f"Privacy policy last updated {months} months ago — getting stale"
    else:
        contribution, rationale = 0, f"Privacy policy refreshed within the last year ({months} months)"

    return ScoreComponent(
        name="privacy_freshness",
        label="Privacy policy freshness",
        contribution=contribution,
        rationale=rationale,
        evidence_url=evidence,
    )


def score_pricing_transparency(
    pricing: PricingSignal | None, evidence: str | None
) -> ScoreComponent | None:
    if pricing is None or pricing.model == PricingModel.UNKNOWN:
        return None
    if pricing.model == PricingModel.CONTACT_SALES:
        return ScoreComponent(
            name="pricing_transparency",
            label="Pricing transparency",
            contribution=5,
            rationale="Contact-sales-only — negotiation lens, no public list price",
            evidence_url=evidence,
        )
    return ScoreComponent(
        name="pricing_transparency",
        label="Pricing transparency",
        contribution=0,
        rationale=f"Public pricing visible ({pricing.model.value.lower().replace('_', '-')})",
        evidence_url=evidence,
    )


def score_subprocessor_transparency(
    subprocs: SubProcessors | None, evidence: str | None
) -> ScoreComponent | None:
    if subprocs is None or not subprocs.publicly_listed or not subprocs.processors:
        return None
    return ScoreComponent(
        name="subprocessor_transparency",
        label="Sub-processor transparency",
        contribution=-5,
        rationale=f"Publicly lists {len(subprocs.processors)} sub-processors — good signal",
        evidence_url=evidence,
    )


def score_operating_health(
    health: OperatingHealth | None, evidence: str | None
) -> ScoreComponent | None:
    if health is None:
        return None

    contribution = 0
    parts: list[str] = []

    if health.recent_breaches:
        contribution += 30
        parts.append(f"{len(health.recent_breaches)} disclosed breach(es)")
    if health.recent_layoffs:
        contribution += 15
        parts.append(f"{len(health.recent_layoffs)} recent layoff(s)")
    if health.leadership_changes:
        contribution += 5
        parts.append(f"{len(health.leadership_changes)} leadership change(s)")
    if health.status_incidents_90d and health.status_incidents_90d > 5:
        contribution += 10
        parts.append(f"{health.status_incidents_90d} status incidents in the last 90 days")

    if contribution == 0:
        return None
    return ScoreComponent(
        name="operating_health",
        label="Operating health",
        contribution=contribution,
        rationale="; ".join(parts),
        evidence_url=evidence,
    )


def score_ownership(
    ownership: Ownership | None, evidence: str | None
) -> ScoreComponent | None:
    """Acquisitions / parent-company status — operational continuity lens."""
    if ownership is None or ownership.status == OwnershipStatus.UNKNOWN:
        return None
    if ownership.status == OwnershipStatus.SUBSIDIARY and ownership.parent:
        return ScoreComponent(
            name="ownership_status",
            label="Ownership status",
            contribution=10,
            rationale=f"Subsidiary of {ownership.parent} — vendor-of-vendor consideration",
            evidence_url=evidence,
        )
    if ownership.status == OwnershipStatus.PRIVATE_PE:
        return ScoreComponent(
            name="ownership_status",
            label="Ownership status",
            contribution=5,
            rationale="PE-owned — typical 3-5yr resale timeline",
            evidence_url=evidence,
        )
    if ownership.status == OwnershipStatus.PUBLIC:
        return ScoreComponent(
            name="ownership_status",
            label="Ownership status",
            contribution=-3,
            rationale=f"Publicly traded ({ownership.ticker})" if ownership.ticker else "Publicly traded",
            evidence_url=evidence,
        )
    return None


# --- red-flag detectors ------------------------------------------------------


@dataclass(slots=True)
class _IntelBundle:
    security: SecurityPosture | None = None
    privacy: PrivacyPolicy | None = None
    pricing: PricingSignal | None = None
    ownership: Ownership | None = None
    subprocessors: SubProcessors | None = None
    operating_health: OperatingHealth | None = None


def detect_red_flags(intel: _IntelBundle, *, today: date | None = None) -> list[RedFlag]:
    today = today or date.today()
    flags: list[RedFlag] = []

    sec = intel.security
    if sec and sec.certifications:
        types = {c.type for c in sec.certifications}
        has_soc2 = bool(types & {CertificationType.SOC2_T1, CertificationType.SOC2_T2})
        has_other = bool(types - {CertificationType.SOC2_T1, CertificationType.SOC2_T2})
        if has_other and not has_soc2:
            flags.append(RedFlag(
                code="soc2_missing",
                label="No SOC 2 attestation",
                detail="Has other certifications but SOC 2 was not detected",
                severity=FlagSeverity.WARN,
            ))

    if intel.privacy and intel.privacy.last_updated:
        months = (
            (today.year - intel.privacy.last_updated.year) * 12
            + (today.month - intel.privacy.last_updated.month)
        )
        if months > 24:
            flags.append(RedFlag(
                code="policy_stale",
                label="Privacy policy stale",
                detail=f"Last updated {months} months ago",
                severity=FlagSeverity.WARN,
            ))

    if intel.ownership and intel.ownership.status == OwnershipStatus.SUBSIDIARY and intel.ownership.parent:
        flags.append(RedFlag(
            code="subsidiary_disclosed",
            label=f"Subsidiary of {intel.ownership.parent}",
            detail="Notable for vendor-of-vendor / contract-novation considerations",
            severity=FlagSeverity.INFO,
        ))

    if intel.operating_health and intel.operating_health.recent_breaches:
        flags.append(RedFlag(
            code="breach_disclosed",
            label="Breach disclosed",
            detail=f"{len(intel.operating_health.recent_breaches)} disclosed breach(es)",
            severity=FlagSeverity.ALERT,
        ))

    if intel.pricing and intel.pricing.model == PricingModel.CONTACT_SALES:
        flags.append(RedFlag(
            code="contact_sales_only",
            label="Contact-sales-only pricing",
            detail="No public list price — negotiation lens",
            severity=FlagSeverity.INFO,
        ))

    return flags


# --- orchestration -----------------------------------------------------------


def _band(score: int) -> RiskBand:
    if score <= GREEN_MAX:
        return RiskBand.GREEN
    if score <= AMBER_MAX:
        return RiskBand.AMBER
    return RiskBand.RED


_SIGNAL_SCHEMAS = {
    "security": SecurityPosture,
    "privacy": PrivacyPolicy,
    "pricing": PricingSignal,
    "ownership": Ownership,
    "subprocessors": SubProcessors,
    "operating_health": OperatingHealth,
}


def compute_risk(intel: _IntelBundle, sources: dict[str, str | None]) -> RiskScore:
    """Pure: score a hydrated intel bundle. No DB, no LLM, deterministic."""
    components: list[ScoreComponent] = []
    for c in (
        score_security(intel.security, sources.get("security")),
        score_privacy_freshness(intel.privacy, sources.get("privacy")),
        score_pricing_transparency(intel.pricing, sources.get("pricing")),
        score_subprocessor_transparency(intel.subprocessors, sources.get("subprocessors")),
        score_operating_health(intel.operating_health, sources.get("operating_health")),
        score_ownership(intel.ownership, sources.get("ownership")),
    ):
        if c is not None:
            components.append(c)

    raw = ANCHOR + sum(c.contribution for c in components)
    score = max(0, min(100, raw))
    return RiskScore(
        score=score,
        band=_band(score),
        components=components,
        red_flags=detect_red_flags(intel),
    )


async def analyze_vendor(session: AsyncSession, vendor: Vendor) -> RiskScore:
    """Pull this vendor's signal_extraction rows, hydrate them, score."""
    rows = (
        await session.execute(
            select(SignalExtraction).where(SignalExtraction.vendor_id == vendor.id)
        )
    ).scalars().all()

    intel = _IntelBundle()
    sources: dict[str, str | None] = {}
    for r in rows:
        schema = _SIGNAL_SCHEMAS.get(r.signal_type)
        if schema is None:
            continue
        setattr(intel, r.signal_type, schema.model_validate(r.payload))
        sources[r.signal_type] = r.source_urls[0] if r.source_urls else None

    return compute_risk(intel, sources)


async def persist_report(
    session: AsyncSession,
    vendor: Vendor,
    risk: RiskScore,
    summary_md: str | None = None,
) -> Report:
    """Append a Report row (history retained for diff vs prior runs)."""
    report = Report(
        vendor_id=vendor.id,
        risk_score=risk.score,
        risk_band=risk.band.value,
        components=[c.model_dump() for c in risk.components],
        red_flags=[f.model_dump() for f in risk.red_flags],
        summary_md=summary_md,
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    return report
