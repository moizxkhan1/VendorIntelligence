"""Stage 2 — URL ranking.

Each DiscoveredUrl row is scored against the six signals we care about
(security, privacy, dpa, sub-processors, pricing, status). The best signal
+ score is written back onto the row. The ranker also dedupes URLs that
canonicalize to the same content (locale prefixes, alias-domain variants),
keeping only the highest-scored variant.

This is the layer that turns 4,568 sitemap matches for "segment.com" (a
brittle keyword grep) into a handful of URLs the fetcher should actually
read.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DiscoveredUrl, Vendor


@dataclass(frozen=True, slots=True)
class SignalDef:
    name: str
    terms: tuple[str, ...]              # path or subdomain segments that signal this
    parents: tuple[str, ...] = ()       # legitimate parent path segments (e.g. /legal/dpa)
    is_subdomain_signal: bool = False   # subdomain-native (status, trust)


SIGNALS: tuple[SignalDef, ...] = (
    SignalDef(
        name="security",
        terms=("security", "trust", "compliance"),
        parents=("legal", "trust"),
        is_subdomain_signal=True,
    ),
    SignalDef(
        name="privacy",
        terms=("privacy", "privacy-policy", "data-protection"),
        parents=("legal",),
    ),
    SignalDef(
        name="dpa",
        terms=("dpa", "data-processing-agreement", "data-processing-addendum"),
        parents=("legal", "trust"),
    ),
    SignalDef(
        name="subprocessors",
        terms=("subprocessors", "sub-processors", "subprocessor", "sub-processor"),
        parents=("legal", "trust"),
    ),
    SignalDef(
        name="pricing",
        terms=("pricing", "plans"),
    ),
    SignalDef(
        name="status",
        terms=("status",),
        is_subdomain_signal=True,
    ),
)

# Path segments that mark blog/help/marketing/taxonomy noise. Any URL
# containing one of these in its path drops out regardless of other matches.
NOISE_SEGMENTS = frozenset({
    "blog", "blogs", "help", "support", "news", "articles", "article",
    "press", "press-releases", "customers", "customer-stories", "case-studies",
    "guides", "guide", "resources", "ebooks", "whitepapers",
    "compare", "vs", "versus", "alternatives",
    "events", "podcast", "podcasts", "webinars",
    "about", "team", "careers", "jobs",
    "templates", "template", "category", "categories", "tag", "tags",
    "search", "explore", "library", "gallery",
    "changelog", "release-notes", "releases",
})

# Locale prefix detector — `/en/`, `/en-us/`, `/de_de/`, etc.
LOCALE_RE = re.compile(r"^[a-z]{2}([-_][a-z]{2,4})?$", re.I)

# Score floor for "this URL is plausibly about this signal." Below it, we
# treat it as a coincidental keyword match and don't categorize it. Tuned so
# legitimate root paths (/security=100, /legal/privacy=80) and known canonicals
# (trust.*, status.*=90) survive while partial-match-at-depth (45) gets cut.
MIN_VIABLE_SCORE = 50

# Hard cap on URLs per signal that progress to fetch + LLM extraction.
# Used by the selection stage downstream to keep extraction cost bounded.
MAX_URLS_PER_SIGNAL = 2


def score_url(url: str, signal: SignalDef) -> int:
    """Roughly [0, 100]. Higher = better candidate for this signal."""
    p = urlparse(url)
    host_parts = p.hostname.split(".") if p.hostname else []
    path_parts = [seg for seg in p.path.split("/") if seg]

    has_locale = bool(path_parts and LOCALE_RE.fullmatch(path_parts[0]))
    canonical_parts = path_parts[1:] if has_locale else path_parts

    score = 0

    # Subdomain match (e.g. trust.acme.com)
    if host_parts and host_parts[0] in signal.terms:
        score = 90 if signal.is_subdomain_signal else 70

    # Path segments
    for i, seg in enumerate(canonical_parts):
        for term in signal.terms:
            if seg == term:
                score = max(score, 100 - 20 * i)
            elif term in seg and len(term) >= 4:
                # Partial match — "privacy" inside "privacy-policy"
                score = max(score, 60 - 15 * i)

    # Legitimate-parent path (e.g. /legal/privacy, /trust/dpa)
    if len(canonical_parts) >= 2 and canonical_parts[0] in signal.parents:
        for term in signal.terms:
            if term == canonical_parts[1] or term in canonical_parts[1]:
                score = max(score, 80)
                break

    # Penalties
    if any(seg in NOISE_SEGMENTS for seg in path_parts):
        score -= 60
    if len(canonical_parts) > 4:
        score -= 15
    if has_locale:
        score -= 3  # mild — we still want it if no canonical exists

    return max(0, score)


@dataclass(slots=True)
class RankedUrl:
    discovered_url: DiscoveredUrl
    signal: str
    score: int


def _best_signal_for(url: str) -> tuple[str | None, int]:
    best_signal: str | None = None
    best_score = 0
    for sig in SIGNALS:
        s = score_url(url, sig)
        if s > best_score:
            best_signal = sig.name
            best_score = s
    return best_signal, best_score


def _canonical_key(url: str, primary_domain: str, aliases: Iterable[str]) -> str:
    """Reduce URL variants of the same content to a single dedupe key.

    Maps alias-domain hosts to the primary, strips locale path prefix,
    drops trailing slash. URL `notion.com/security` and `notion.so/en/security`
    will both yield `notion.so/security` for vendor (primary=notion.so,
    aliases=[notion.com]).
    """
    p = urlparse(url)
    host = (p.hostname or "").lower()
    for alias in aliases:
        if host == alias or host.endswith("." + alias):
            host = host.replace(alias, primary_domain, 1)
            break

    parts = [seg for seg in p.path.split("/") if seg]
    if parts and LOCALE_RE.fullmatch(parts[0]):
        parts = parts[1:]
    return f"{host}/{'/'.join(parts)}"


def rank(
    urls: Iterable[DiscoveredUrl],
    primary_domain: str,
    aliases: Iterable[str] = (),
) -> list[RankedUrl]:
    """Score every URL, keep only canonical winners scoring >= MIN_VIABLE_SCORE."""
    aliases_t = tuple(aliases)
    canonical_seen: dict[str, RankedUrl] = {}

    for du in urls:
        signal, sc = _best_signal_for(du.url)
        if signal is None or sc < MIN_VIABLE_SCORE:
            continue
        key = _canonical_key(du.url, primary_domain, aliases_t)
        existing = canonical_seen.get(key)
        if existing is None or existing.score < sc:
            canonical_seen[key] = RankedUrl(du, signal, sc)
    return list(canonical_seen.values())


async def rank_vendor(session: AsyncSession, vendor: Vendor) -> int:
    """Score & persist categories on the vendor's DiscoveredUrl rows.
    Idempotent: any prior category/score is cleared before assignment.
    Returns count of URLs that received a category."""
    rows = (
        await session.execute(select(DiscoveredUrl).where(DiscoveredUrl.vendor_id == vendor.id))
    ).scalars().all()

    await session.execute(
        update(DiscoveredUrl)
        .where(DiscoveredUrl.vendor_id == vendor.id)
        .values(category=None, score=None)
    )

    aliases = tuple(vendor.aliases or ())
    ranked = rank(rows, vendor.domain, aliases)

    for r in ranked:
        r.discovered_url.category = r.signal
        r.discovered_url.score = float(r.score)

    await session.commit()
    return len(ranked)


async def top_urls_per_signal(
    session: AsyncSession,
    vendor: Vendor,
    n: int = 3,
) -> dict[str, list[DiscoveredUrl]]:
    """Top-N URLs by score for each signal the vendor has any data for."""
    rows = (
        await session.execute(
            select(DiscoveredUrl)
            .where(
                DiscoveredUrl.vendor_id == vendor.id,
                DiscoveredUrl.category.is_not(None),
            )
            .order_by(DiscoveredUrl.score.desc())
        )
    ).scalars().all()

    bucketed: dict[str, list[DiscoveredUrl]] = {}
    for r in rows:
        bucketed.setdefault(r.category, []).append(r)
    return {sig: items[:n] for sig, items in bucketed.items()}
