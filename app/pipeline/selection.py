"""Stage 2.5 — LLM-driven URL selection.

After heuristic ranking, several URLs can score similarly within a signal
(e.g. for Linear's security signal: /security/vulnerability, trust.linear.app,
and /docs/security all score 80-100). The heuristic understands path
structure but not semantics — it can't tell that /security/vulnerability is
a vuln-disclosure page rather than the actual security posture page.

This stage feeds the LLM the per-signal candidate set (URL + score only —
no page content yet) and asks it to pick the 1-2 URLs most likely to
contain the canonical signal content. Then the fetcher only spends
on those URLs.

Cost optimization: the LLM call is *skipped entirely* when every signal
already has at most MAX_URLS_PER_SIGNAL candidates from heuristics — a
common fast path for well-organized vendors.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.llm import LLMProvider
from app.models import DiscoveredUrl, Vendor
from app.pipeline.ranking import MAX_URLS_PER_SIGNAL

# How many heuristic candidates per signal to feed the LLM. Larger gives the
# model more options; smaller saves tokens. Five is plenty given heuristic
# ranking has already filtered out noise.
HEURISTIC_TOP_K = 5

SIGNALS = ("security", "privacy", "dpa", "subprocessors", "pricing", "status")


class URLSelection(BaseModel):
    """LLM-curated picks per signal. Empty list = no good candidate."""

    security: list[str] = Field(default_factory=list)
    privacy: list[str] = Field(default_factory=list)
    dpa: list[str] = Field(default_factory=list)
    subprocessors: list[str] = Field(default_factory=list)
    pricing: list[str] = Field(default_factory=list)
    status: list[str] = Field(default_factory=list)


_SYSTEM_TEMPLATE = """You pick which URLs from a candidate list contain the canonical content for each signal we care about for SaaS-vendor due diligence. Be conservative — pick at most {max} URLs per signal. If no URL is clearly the right one, return an empty list for that signal.

Signals:
- security: vendor's main security/trust posture page (SOC 2, ISO 27001, HIPAA mentions, etc.). Trust portals (trust.{{domain}}, trustcenter.{{domain}}) are usually best.
- privacy: privacy policy / data-protection notice
- dpa: data processing agreement / addendum (DPA)
- subprocessors: list of named sub-processors / third parties
- pricing: pricing tiers / plans page
- status: live status / uptime page

Prefer:
- Root paths over nested (/security beats /docs/security)
- Canonical pages over vulnerability-disclosure or compliance subpages
- Privacy POLICY pages over privacy-related blog posts
- The lowest-numbered locale variant when content is duplicated across locales

You MUST return only URLs that appeared in the input. Do not invent URLs."""


def _format_candidates(candidates: dict[str, list[DiscoveredUrl]]) -> str:
    lines: list[str] = []
    for signal in SIGNALS:
        urls = candidates.get(signal, [])
        if not urls:
            continue
        lines.append(f"## {signal}")
        for u in urls:
            score = "—" if u.score is None else f"{u.score:.0f}"
            lines.append(f"  [{score}] {u.url}")
        lines.append("")
    return "\n".join(lines).strip()


async def select_for_extraction(
    llm: LLMProvider,
    vendor: Vendor,
    candidates: dict[str, list[DiscoveredUrl]],
) -> dict[str, list[DiscoveredUrl]]:
    """Reduce per-signal candidate lists to at most MAX_URLS_PER_SIGNAL.

    Skips the LLM entirely when every signal already has <= MAX_URLS_PER_SIGNAL
    candidates — saves a call when the heuristic was already tight.
    """
    if not any(candidates.values()):
        return {}

    if all(len(urls) <= MAX_URLS_PER_SIGNAL for urls in candidates.values()):
        return {sig: urls for sig, urls in candidates.items() if urls}

    trimmed = {sig: urls[:HEURISTIC_TOP_K] for sig, urls in candidates.items() if urls}

    user_prompt = (
        f"Vendor domain: {vendor.domain}\n"
        f"Aliases: {', '.join(vendor.aliases) if vendor.aliases else '(none)'}\n\n"
        f"Heuristic candidates per signal (score in brackets):\n\n"
        f"{_format_candidates(trimmed)}"
    )

    selection = await llm.extract(
        system=_SYSTEM_TEMPLATE.format(max=MAX_URLS_PER_SIGNAL),
        user=user_prompt,
        schema=URLSelection,
    )

    return _resolve(selection, trimmed)


def _resolve(
    selection: URLSelection,
    candidates: dict[str, list[DiscoveredUrl]],
) -> dict[str, list[DiscoveredUrl]]:
    """Map URL strings the LLM picked back to DiscoveredUrl rows."""
    by_url: dict[str, DiscoveredUrl] = {u.url: u for urls in candidates.values() for u in urls}

    out: dict[str, list[DiscoveredUrl]] = {}
    for signal in SIGNALS:
        picked = getattr(selection, signal, [])[:MAX_URLS_PER_SIGNAL]
        rows = [by_url[u] for u in picked if u in by_url]
        if rows:
            out[signal] = rows
    return out
