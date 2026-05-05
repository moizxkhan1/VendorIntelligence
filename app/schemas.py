"""Pydantic schemas — API I/O models and LLM-extraction targets.

The signal models below are the structured output contract for the LLM
extraction stage. Each maps to one row in `signal_extraction` once persisted.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ----------------------------------------------------------------------------
# Vendor I/O (used by the CRUD routes)
# ----------------------------------------------------------------------------


def normalize_domain(s: str) -> str:
    """Lowercase, strip protocol/www/path/whitespace. Returns '' for empty/invalid input."""
    s = (s or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("/", 1)[0].strip().rstrip(".")
    return s


class VendorCreate(BaseModel):
    domain: str
    display_name: str | None = None
    aliases: list[str] = Field(default_factory=list)

    @field_validator("domain", mode="before")
    @classmethod
    def _normalize_domain(cls, v: str) -> str:
        d = normalize_domain(v)
        if not d:
            raise ValueError("domain is required")
        return d

    @field_validator("display_name", mode="before")
    @classmethod
    def _normalize_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None

    @field_validator("aliases", mode="before")
    @classmethod
    def _normalize_aliases(cls, v: object) -> list[str]:
        if v is None or v == "":
            return []
        parts = re.split(r"[,\s]+", v) if isinstance(v, str) else list(v)
        out: list[str] = []
        seen: set[str] = set()
        for p in parts:
            d = normalize_domain(str(p))
            if d and d not in seen:
                seen.add(d)
                out.append(d)
        return out


class VendorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    display_name: str | None
    aliases: list[str]
    added_at: datetime


# ----------------------------------------------------------------------------
# Signal schemas (LLM extraction targets)
# ----------------------------------------------------------------------------


class CertificationType(str, Enum):
    SOC2_T1 = "SOC2_T1"
    SOC2_T2 = "SOC2_T2"
    SOC1 = "SOC1"
    ISO27001 = "ISO27001"
    ISO27017 = "ISO27017"
    ISO27018 = "ISO27018"
    ISO27701 = "ISO27701"
    ISO42001 = "ISO42001"
    HIPAA = "HIPAA"
    FEDRAMP_MODERATE = "FEDRAMP_MODERATE"
    FEDRAMP_HIGH = "FEDRAMP_HIGH"
    PCI_DSS = "PCI_DSS"
    GDPR = "GDPR"
    CCPA = "CCPA"
    CSA_STAR = "CSA_STAR"
    OTHER = "OTHER"


class Certification(BaseModel):
    type: CertificationType
    detail: str | None = Field(
        default=None,
        description="Free-text qualifier — e.g. 'Type II', 'High', 'FINRA-aligned'",
    )
    last_audit_year: int | None = None


class SecurityPosture(BaseModel):
    certifications: list[Certification] = Field(default_factory=list)
    bug_bounty: bool | None = Field(
        default=None, description="Public bug-bounty program present"
    )
    notes: str | None = Field(default=None, description="One short sentence summarizing the posture")


class SubProcessor(BaseModel):
    name: str
    purpose: str | None = None
    region: str | None = None


class SubProcessors(BaseModel):
    processors: list[SubProcessor] = Field(default_factory=list)
    publicly_listed: bool = Field(
        default=False, description="True if the list is published on the website"
    )
    list_url: str | None = None


class GdprMechanism(str, Enum):
    SCC = "SCC"
    DPF = "DPF"
    BCR = "BCR"
    ADEQUACY = "ADEQUACY"
    NONE_DECLARED = "NONE_DECLARED"
    UNKNOWN = "UNKNOWN"


class DataResidency(str, Enum):
    US = "US"
    EU = "EU"
    BOTH = "BOTH"
    GLOBAL = "GLOBAL"
    UNKNOWN = "UNKNOWN"


class PrivacyPolicy(BaseModel):
    last_updated: date | None = None
    gdpr_mechanism: GdprMechanism = GdprMechanism.UNKNOWN
    data_residency: DataResidency = DataResidency.UNKNOWN
    notes: str | None = None


class PricingModel(str, Enum):
    FREEMIUM = "FREEMIUM"
    TIERED = "TIERED"
    CONTACT_SALES = "CONTACT_SALES"
    HYBRID = "HYBRID"
    USAGE_BASED = "USAGE_BASED"
    UNKNOWN = "UNKNOWN"


class PricingTier(BaseModel):
    name: str
    monthly_usd: float | None = None
    notes: str | None = None


class PricingSignal(BaseModel):
    model: PricingModel = PricingModel.UNKNOWN
    starting_price_usd: float | None = None
    tiers: list[PricingTier] = Field(default_factory=list)
    enterprise_only: bool | None = None
    notes: str | None = None


class OwnershipStatus(str, Enum):
    PUBLIC = "PUBLIC"
    PRIVATE_VC = "PRIVATE_VC"
    PRIVATE_PE = "PRIVATE_PE"
    SUBSIDIARY = "SUBSIDIARY"
    BOOTSTRAPPED = "BOOTSTRAPPED"
    UNKNOWN = "UNKNOWN"


class FundingRound(BaseModel):
    stage: str | None = Field(default=None, description="Series A / B / C / IPO / etc.")
    amount_usd: float | None = None
    closed_on: date | None = None


class Ownership(BaseModel):
    status: OwnershipStatus = OwnershipStatus.UNKNOWN
    parent: str | None = Field(default=None, description="Parent company name if subsidiary")
    founded_year: int | None = None
    last_round: FundingRound | None = None
    ticker: str | None = None


class IncidentSummary(BaseModel):
    date_iso: date | None = None
    description: str
    source_url: str | None = None


class OperatingHealth(BaseModel):
    recent_layoffs: list[IncidentSummary] = Field(default_factory=list)
    recent_breaches: list[IncidentSummary] = Field(default_factory=list)
    leadership_changes: list[IncidentSummary] = Field(default_factory=list)
    status_incidents_90d: int | None = None
    notes: str | None = None


class VendorIntelligence(BaseModel):
    """All signals for one vendor. Any signal with no supporting evidence is None.

    This is the top-level LLM extraction target — a single call returns this
    blob, then the persister splits it into one signal_extraction row per
    non-null signal.
    """

    security: SecurityPosture | None = None
    subprocessors: SubProcessors | None = None
    privacy: PrivacyPolicy | None = None
    pricing: PricingSignal | None = None
    ownership: Ownership | None = None
    operating_health: OperatingHealth | None = None
