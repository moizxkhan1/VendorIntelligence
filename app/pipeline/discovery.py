"""Stage 1 — URL discovery for a vendor.

Pulls /robots.txt, follows declared sitemaps (recursing into sitemap indexes),
falls back to /sitemap.xml, and probes well-known subdomains for trust portals
and status pages. Returns a DiscoveryResult; persistence is in `persist_discovery`
so this module stays I/O-pure and easy to test against captured fixtures.
"""

from __future__ import annotations

import asyncio
import gzip
import re
from collections.abc import Iterable
from dataclasses import dataclass

from curl_cffi import AsyncSession
from curl_cffi.requests import RequestsError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from app.models import DiscoveredUrl, Vendor

IMPERSONATE = "chrome120"
TIMEOUT_S = 25
MAX_SITEMAP_DEPTH = 2
MAX_CHILD_SITEMAPS = 25
SUBDOMAINS: tuple[str, ...] = ("trust", "status", "security", "legal", "compliance")

# URL keywords that flag signal-relevant pages. Used by DiscoveryResult.relevant
# (which the persist step writes) and by the ranker downstream.
SIGNAL_RE: re.Pattern[str] = re.compile(
    r"(security|trust|privacy|legal|dpa|data-process|subprocess|sub-process|pricing|"
    r"compliance|status|terms|cookie|gdpr|ccpa|iso[-_ ]?27001|soc[-_ ]?2|hipaa|pci|fedramp)",
    re.I,
)


@dataclass(slots=True)
class DiscoveryResult:
    domain: str
    aliases: tuple[str, ...]
    sitemap_sources: list[str]            # sitemap URLs we successfully read from
    urls: list[str]                        # all harvested URLs (deduped, sorted)
    subdomain_probes: dict[str, int]       # url -> HTTP status (0 = unreachable)

    @property
    def total(self) -> int:
        return len(self.urls)

    @property
    def relevant(self) -> list[str]:
        return [u for u in self.urls if SIGNAL_RE.search(u)]

    @property
    def reachable_subdomains(self) -> dict[str, int]:
        return {u: s for u, s in self.subdomain_probes.items() if 200 <= s < 400}


async def discover_urls(domain: str, aliases: Iterable[str] = ()) -> DiscoveryResult:
    """Harvest sitemap URLs + probe subdomains for a vendor."""
    aliases_t = tuple(aliases)
    hosts = (domain, *aliases_t)

    async with AsyncSession(impersonate=IMPERSONATE, timeout=TIMEOUT_S) as client:
        url_set: set[str] = set()
        sources: list[str] = []
        for host in hosts:
            urls, srcs = await _harvest_host(client, host)
            url_set.update(urls)
            sources.extend(srcs)

        probe_targets = [f"https://{s}.{h}" for h in hosts for s in SUBDOMAINS]
        statuses = await asyncio.gather(*(_probe(client, u) for u in probe_targets))
        probes = dict(zip(probe_targets, statuses))

    return DiscoveryResult(
        domain=domain,
        aliases=aliases_t,
        sitemap_sources=sorted(set(sources)),
        urls=sorted(url_set),
        subdomain_probes=probes,
    )


async def persist_discovery(
    session: DbSession,
    vendor: Vendor,
    result: DiscoveryResult,
) -> int:
    """Write signal-relevant URLs + reachable subdomains to discovered_url.

    Idempotent: rows already present (vendor_id + url) are skipped.
    Returns count of newly inserted rows.
    """
    existing_q = select(DiscoveredUrl.url).where(DiscoveredUrl.vendor_id == vendor.id)
    existing: set[str] = set((await session.execute(existing_q)).scalars().all())

    to_insert: list[DiscoveredUrl] = []
    for url in result.relevant:
        if url in existing:
            continue
        existing.add(url)
        to_insert.append(DiscoveredUrl(vendor_id=vendor.id, url=url, source="sitemap"))

    for url, status in result.reachable_subdomains.items():
        if url in existing:
            continue
        existing.add(url)
        to_insert.append(
            DiscoveredUrl(vendor_id=vendor.id, url=url, source="subdomain", http_status=status)
        )

    if not to_insert:
        return 0
    session.add_all(to_insert)
    await session.commit()
    return len(to_insert)


# --- internals --------------------------------------------------------------


async def _harvest_host(client: AsyncSession, host: str) -> tuple[set[str], list[str]]:
    urls: set[str] = set()
    sources: list[str] = []
    seen: set[str] = set()

    robots = await _fetch(client, f"https://{host}/robots.txt")
    if robots is not None and robots.status_code == 200:
        for line in robots.text.splitlines():
            m = re.match(r"\s*Sitemap:\s*(\S+)", line, re.I)
            if not m:
                continue
            sm = m.group(1).strip()
            got = await _harvest_sitemap(client, sm, seen)
            if got:
                sources.append(sm)
            urls.update(got)

    fallback = f"https://{host}/sitemap.xml"
    if fallback not in seen:
        got = await _harvest_sitemap(client, fallback, seen)
        if got:
            sources.append(fallback)
        urls.update(got)

    return urls, sources


async def _harvest_sitemap(
    client: AsyncSession, url: str, seen: set[str], depth: int = 0
) -> set[str]:
    if depth > MAX_SITEMAP_DEPTH or url in seen:
        return set()
    seen.add(url)
    r = await _fetch(client, url)
    if r is None or r.status_code != 200:
        return set()
    page_urls, children = _parse_sitemap(r.content)
    out: set[str] = set(page_urls)
    if children:
        nested = await asyncio.gather(
            *(_harvest_sitemap(client, c, seen, depth + 1) for c in children[:MAX_CHILD_SITEMAPS])
        )
        for n in nested:
            out.update(n)
    return out


def _parse_sitemap(content: bytes) -> tuple[list[str], list[str]]:
    """Returns (page_urls, child_sitemaps). Handles raw XML and .xml.gz."""
    if not content:
        return [], []
    if content[:2] == b"\x1f\x8b":
        try:
            content = gzip.decompress(content)
        except Exception:
            return [], []
    text = content.decode("utf-8", errors="ignore")
    is_index = "<sitemapindex" in text.lower()
    locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", text, flags=re.I)
    if is_index:
        return [], locs
    return locs, []


async def _fetch(client: AsyncSession, url: str):
    try:
        return await client.get(url)
    except RequestsError:
        return None


async def _probe(client: AsyncSession, url: str) -> int:
    """HEAD first (cheap), GET fallback. 0 = unreachable."""
    try:
        r = await client.head(url)
        if 200 <= r.status_code < 400:
            return r.status_code
    except RequestsError:
        pass
    try:
        r = await client.get(url)
        return r.status_code
    except RequestsError:
        return 0
