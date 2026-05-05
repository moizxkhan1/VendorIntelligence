"""Stage 4 — LLM signal extraction.

For each vendor, gather the scraped pages selected by the LLM ranker
(typically 1-2 per signal) and extract structured signal data in a single
LLM call returning a `VendorIntelligence` blob. The persister then splits
that blob into one `signal_extraction` row per non-null signal.

A single per-vendor call is the cheap path: ~20 LLM calls total for the
19 starter vendors, vs ~120 if we called per page.
"""

from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import LLMProvider
from app.models import ScrapedPage, SignalExtraction, Vendor
from app.schemas import VendorIntelligence

MAX_TEXT_PER_PAGE = 30_000   # bytes — keep prompt manageable; salient content is up top
SIGNAL_TYPES = ("security", "subprocessors", "privacy", "pricing", "ownership", "operating_health")

EXTRACTION_SYSTEM = """You analyze publicly-scraped SaaS vendor pages to extract structured intelligence for procurement / due-diligence reviews.

You will receive page-text excerpts from one vendor's website (each page is delimited by a `=== {url} ===` header). Extract the following signals into the structured output. Set any field you have NO evidence for to null. Do not invent or guess.

Signals:

1. **security** — certifications visible on the page (SOC 2 Type I/II, ISO 27001/27017/27018/27701/42001, HIPAA, FedRAMP Moderate/High, PCI-DSS, CSA STAR), bug-bounty program presence, and a one-sentence posture note.
2. **subprocessors** — named third-party companies the vendor lists as data sub-processors. Set `publicly_listed=true` only if the list is on a publicly-accessible page.
3. **privacy** — last-updated date of the privacy policy, GDPR transfer mechanism (SCC / DPF / BCR / ADEQUACY / NONE_DECLARED), data residency (US / EU / BOTH / GLOBAL).
4. **pricing** — model (FREEMIUM / TIERED / CONTACT_SALES / HYBRID / USAGE_BASED), starting USD price if shown, tiers, whether the product is enterprise-only.
5. **ownership** — public / private-VC / private-PE / subsidiary, parent company name if subsidiary, founded year, latest funding round, stock ticker.
6. **operating_health** — recent layoffs, breaches, leadership changes mentioned on the site (rare). Most operating-health signals come from external sources we don't have here; leave fields null if no evidence.

Rules:
- Quote facts only when the page text contains them. If a field is absent, it stays null.
- Use ISO date format (YYYY-MM-DD) for any dates.
- Be precise about certification types — distinguish Type I from Type II, ISO 27001 from ISO 27701, FedRAMP Moderate from High.
- Notes fields should be short (one sentence each)."""


async def extract_for_vendor(
    llm: LLMProvider,
    vendor: Vendor,
    pages: list[ScrapedPage],
) -> VendorIntelligence:
    """Single LLM call returning structured data for all six signals.

    Returns an empty VendorIntelligence (all None) when there are no usable pages.
    """
    usable = [p for p in pages if p.content_text and p.http_status and 200 <= p.http_status < 400]
    if not usable:
        return VendorIntelligence()

    user_prompt = (
        f"Vendor: {vendor.domain}\n"
        f"Aliases: {', '.join(vendor.aliases) if vendor.aliases else '(none)'}\n\n"
        f"Pages (excerpts; truncated where long):\n\n"
        f"{_format_pages(usable)}"
    )

    return await llm.extract(
        system=EXTRACTION_SYSTEM,
        user=user_prompt,
        schema=VendorIntelligence,
    )


async def persist_extraction(
    session: AsyncSession,
    vendor: Vendor,
    intel: VendorIntelligence,
    source_urls: list[str],
) -> list[SignalExtraction]:
    """Replace prior signal_extraction rows for this vendor with the new blob.

    Splits VendorIntelligence into one row per non-null signal so downstream
    queries can pull a single signal without deserializing the whole blob.
    """
    await session.execute(delete(SignalExtraction).where(SignalExtraction.vendor_id == vendor.id))

    rows: list[SignalExtraction] = []
    for signal in SIGNAL_TYPES:
        payload = getattr(intel, signal)
        if payload is None:
            continue
        rows.append(
            SignalExtraction(
                vendor_id=vendor.id,
                signal_type=signal,
                payload=payload.model_dump(mode="json"),
                source_urls=list(source_urls),
            )
        )

    session.add_all(rows)
    await session.commit()
    return rows


# --- internals --------------------------------------------------------------


def _format_pages(pages: list[ScrapedPage]) -> str:
    sections: list[str] = []
    for p in pages:
        body = p.content_text[:MAX_TEXT_PER_PAGE]
        sections.append(f"=== {p.url} ===\n{body}")
    return "\n\n".join(sections)
