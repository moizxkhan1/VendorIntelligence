"""Stage 3 — Fetcher.

curl-cffi (Chrome TLS impersonation) is the primary path. We escalate to a
Playwright-driven Chromium when:
  - the URL host is on a known JS-only list (SafeBase trust portals, etc.),
  - the curl-cffi response renders to thin body text and contains a
    SafeBase / Drata / generic-script-loader fingerprint, or
  - the caller forces it via force_browser=True.

`fetch_page()` never raises for HTTP-level failures — fundamental fetch
failures surface as `FetchResult(http_status=0, error=...)` so the worker
can record the loud-fail signal the brief explicitly asks for.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from curl_cffi import AsyncSession
from curl_cffi.requests import RequestsError
from sqlalchemy.ext.asyncio import AsyncSession as DbSession

from app.models import DiscoveredUrl, ScrapedPage
from app.pipeline.browser import get_browser

IMPERSONATE = "chrome120"
TIMEOUT_S = 25
NAV_TIMEOUT_MS = 30_000
THIN_TEXT_THRESHOLD = 500    # below this, JS-rendered shells are likely
MAX_HTML_BYTES = 500_000     # cap row size; huge pages are 99% navigation chrome
MAX_TEXT_BYTES = 200_000

# Hosts and HTML fingerprints that mean "curl-cffi got a bootstrap shell only —
# the real content arrives via JS." Maintained as we discover more.
JS_HOST_HINTS: tuple[str, ...] = (
    "trust.anthropic.com",
    "trust.intercom.com",
    "trustcenter.lattice.com",
    "trust.ramp.com",
    "trust-portal.brex.com",
    "trust.coderabbit.ai",
)
JS_HTML_FINGERPRINTS: tuple[str, ...] = (
    "safebase.io",
    "data-safebase",
    "drata-trust",
    "trustcloud",
)


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    http_status: int
    content_html: str
    content_text: str
    used_browser: bool
    fetched_at: datetime
    error: str | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content_text.encode("utf-8")).hexdigest()

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.http_status < 400


async def fetch_page(url: str, *, force_browser: bool = False) -> FetchResult:
    """Fetch a single URL. Returns a FetchResult; never raises."""
    if not force_browser:
        result = await _fetch_curl(url)
        if result is None:
            return _failed(url, error="curl-cffi: connection failed", used_browser=False)
        if not result.ok:
            return result
        if not _needs_browser(result.content_html, result.content_text, url):
            return result

    return await _fetch_browser(url)


async def persist_page(
    session: DbSession,
    discovered_url: DiscoveredUrl | None,
    result: FetchResult,
) -> ScrapedPage:
    page = ScrapedPage(
        discovered_url_id=discovered_url.id if discovered_url else None,
        url=result.url,
        final_url=result.final_url,
        http_status=result.http_status,
        content_html=result.content_html,
        content_text=result.content_text,
        content_hash=result.content_hash,
        used_browser=result.used_browser,
        error=result.error,
        fetched_at=result.fetched_at,
    )
    session.add(page)
    await session.commit()
    await session.refresh(page)
    return page


# --- internals --------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _failed(url: str, *, error: str, used_browser: bool) -> FetchResult:
    return FetchResult(
        url=url,
        final_url=url,
        http_status=0,
        content_html="",
        content_text="",
        used_browser=used_browser,
        fetched_at=_utcnow(),
        error=error,
    )


def _truncate(s: str, max_bytes: int) -> str:
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "template"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text)


def _needs_browser(html: str, text: str, url: str) -> bool:
    if any(host in url for host in JS_HOST_HINTS):
        return True
    lower = html.lower()
    if any(fp in lower for fp in JS_HTML_FINGERPRINTS):
        return True
    if len(text) < THIN_TEXT_THRESHOLD and "<script" in lower:
        return True
    return False


async def _fetch_curl(url: str) -> FetchResult | None:
    try:
        async with AsyncSession(impersonate=IMPERSONATE, timeout=TIMEOUT_S) as client:
            r = await client.get(url, allow_redirects=True)
    except RequestsError:
        return None
    html = _truncate(r.text, MAX_HTML_BYTES)
    text = _truncate(_html_to_text(html), MAX_TEXT_BYTES)
    return FetchResult(
        url=url,
        final_url=str(r.url),
        http_status=r.status_code,
        content_html=html,
        content_text=text,
        used_browser=False,
        fetched_at=_utcnow(),
    )


async def _fetch_browser(url: str) -> FetchResult:
    try:
        browser = await get_browser()
        ctx = await browser.new_context()
        page = await ctx.new_page()
        try:
            response = await page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT_MS)
            html = await page.content()
            # Trust portals (SafeBase/Drata) often finish `networkidle` while
            # the body is still hydrating with the actual content. If we got a
            # thin shell, give it a grace period and re-read.
            if len(_html_to_text(html)) < THIN_TEXT_THRESHOLD:
                await page.wait_for_timeout(3000)
                html = await page.content()
            final_url = page.url
            status = response.status if response is not None else 0
        finally:
            await ctx.close()
    except Exception as e:
        return _failed(url, error=f"playwright: {type(e).__name__}: {e}", used_browser=True)

    html_t = _truncate(html, MAX_HTML_BYTES)
    text_t = _truncate(_html_to_text(html_t), MAX_TEXT_BYTES)
    return FetchResult(
        url=url,
        final_url=final_url,
        http_status=status,
        content_html=html_t,
        content_text=text_t,
        used_browser=True,
        fetched_at=_utcnow(),
    )
