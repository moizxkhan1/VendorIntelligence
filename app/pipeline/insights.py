"""Cross-vendor aggregations for the /insights page.

Pure functions over already-persisted SignalExtraction payloads. Each
returns a small dataclass the template renders directly. Kept separate
from the route so we can unit-test the aggregations without HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Cert types we display as columns in the compliance grid. Lumping the two
# FedRAMP variants together because the grid is dense enough already; the
# detail page still distinguishes them.
GRID_CERTS: tuple[str, ...] = (
    "SOC2_T2",
    "SOC2_T1",
    "ISO27001",
    "HIPAA",
    "PCI_DSS",
    "FEDRAMP",
)
FEDRAMP_VARIANTS = frozenset({"FEDRAMP_MODERATE", "FEDRAMP_HIGH"})


@dataclass(slots=True)
class ConcentrationEntry:
    name: str
    vendors: list[str]

    @property
    def count(self) -> int:
        return len(self.vendors)


@dataclass(slots=True)
class ComplianceRow:
    domain: str
    certs: dict[str, bool]
    missing_soc2: bool


@dataclass(slots=True)
class FreshnessEntry:
    domain: str
    last_updated: date
    months_old: int

    @property
    def band(self) -> str:
        if self.months_old > 24:
            return "red"
        if self.months_old > 12:
            return "amber"
        return "green"


def compute_concentration(
    sub_extractions: list[tuple[str, dict]],
    *,
    min_count: int = 2,
) -> list[ConcentrationEntry]:
    """sub_extractions: list of (vendor_domain, subprocessors_payload).

    Returns sub-processors used by `min_count` or more vendors, descending
    by count. The hidden-lock-in radar.
    """
    by_name: dict[str, set[str]] = {}
    for domain, payload in sub_extractions:
        for proc in payload.get("processors", []) or []:
            name = (proc.get("name") or "").strip()
            if not name:
                continue
            by_name.setdefault(name, set()).add(domain)

    rows = [
        ConcentrationEntry(name=name, vendors=sorted(domains))
        for name, domains in by_name.items()
        if len(domains) >= min_count
    ]
    rows.sort(key=lambda r: (-r.count, r.name.lower()))
    return rows


def compute_compliance_grid(
    security_extractions: list[tuple[str, dict]],
) -> list[ComplianceRow]:
    """security_extractions: list of (vendor_domain, security_payload)."""
    rows: list[ComplianceRow] = []
    for domain, payload in security_extractions:
        types = {c.get("type") for c in payload.get("certifications", []) or []}

        certs: dict[str, bool] = {}
        for col in GRID_CERTS:
            if col == "FEDRAMP":
                certs[col] = bool(types & FEDRAMP_VARIANTS)
            else:
                certs[col] = col in types

        missing_soc2 = not (certs["SOC2_T1"] or certs["SOC2_T2"])
        rows.append(ComplianceRow(domain=domain, certs=certs, missing_soc2=missing_soc2))

    rows.sort(key=lambda r: (not r.missing_soc2, r.domain))  # missing-soc2 first
    return rows


def compute_freshness(
    privacy_extractions: list[tuple[str, dict]],
    *,
    today: date | None = None,
) -> list[FreshnessEntry]:
    """privacy_extractions: list of (vendor_domain, privacy_payload).
    Excludes vendors whose policy lacks a `last_updated` date."""
    today = today or date.today()
    out: list[FreshnessEntry] = []
    for domain, payload in privacy_extractions:
        raw = payload.get("last_updated")
        if not raw:
            continue
        try:
            last = date.fromisoformat(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        months = (today.year - last.year) * 12 + (today.month - last.month)
        out.append(FreshnessEntry(domain=domain, last_updated=last, months_old=months))

    out.sort(key=lambda e: e.months_old, reverse=True)  # stalest first
    return out


@dataclass(slots=True)
class InsightsBundle:
    concentration: list[ConcentrationEntry] = field(default_factory=list)
    compliance: list[ComplianceRow] = field(default_factory=list)
    freshness: list[FreshnessEntry] = field(default_factory=list)
    vendors_analyzed: int = 0
