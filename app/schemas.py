"""Pydantic schemas for API I/O and pipeline payloads.

Pipeline-stage signal schemas (SecurityPosture, SubProcessors, etc.) will land here
as the extraction stage is built; for now this module only carries the Vendor I/O models.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
