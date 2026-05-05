# VendorIntelligence

Vendor intelligence reports for SaaS procurement. Given a list of vendor domains, produces a per-vendor renewal-risk report covering **security posture, sub-processors, privacy & data residency, pricing, ownership, and operating-health flags** — plus a composite 0–100 risk score with attributed components and red flags.

Built for the kind of finance / legal / procurement reviewer who needs to sign off on a renewal in five minutes, not five hours.

## What it does

For each vendor domain you add:

1. **Discovers URLs** by reading `robots.txt` + sitemap indexes and probing well-known subdomains (`trust.`, `status.`, `security.`, `legal.`, `compliance.`).
2. **Ranks them** with a deterministic URL scorer (path specificity, locale dedupe, alias-domain collapse, blog/help noise penalty).
3. **LLM-selects** the canonical 1–2 URLs per signal, so we never extract from more than two pages per signal regardless of how messy the vendor's site is.
4. **Fetches** them with `curl-cffi` (Chrome 120 TLS impersonation defeats most bot blocks) and falls back to **Playwright** when the response looks like a hydrating SafeBase / Drata trust-portal shell.
5. **Extracts** structured signals via the configured LLM into Pydantic-validated schemas.
6. **Scores** the vendor with an anchored, attributed risk model — each component contributes a documented weight and an evidence URL, so finance can audit one weight at a time.

A **`/insights`** view rolls findings up across the portfolio: concentration risk (which sub-processors appear across multiple of your vendors), compliance-coverage grid, and privacy-policy freshness ranking.

## Quickstart

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/Scripts/activate    # Git Bash on Windows
# .venv\Scripts\activate          # PowerShell on Windows
# source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
playwright install chromium       # one-time, ~150 MB

cp .env.example .env              # then fill in LLM_API_KEY

uvicorn app.main:app --reload
```

Open `http://localhost:8000`. Health probe at `/healthz`.

## How to use it

1. **Add vendors.** Use the form on `/`, paste a list into the bulk-import textarea, or click **Seed 19 starter vendors** to load the assessment brief's set.
2. **Click Run analysis.** You're routed to `/reports`, which polls live as each vendor moves through the pipeline.
3. **View a report.** Click **View report** on any row for the full breakdown: risk score, components with evidence, red flags, and per-signal cards labeled `Found` / `Not detected` / `Could not analyze` (with a one-line reason for empty cards). Each report carries a "since previous run" diff if a prior report exists.
4. **Cross-vendor view.** Visit `/insights` for concentration risk, the compliance grid, and freshness ranking.
5. **Export.** Each report is available as JSON at `/reports/{vendor_id}.json`.

## Configuration

All env vars are optional except `LLM_API_KEY`. See `.env.example`.

| Var | Purpose | Default |
|---|---|---|
| `LLM_PROVIDER` | `openai` / `anthropic` / `openai-compat` | `openai` |
| `LLM_MODEL` | model id (e.g. `gpt-5-mini`, `gpt-5.4-mini`, `claude-sonnet-4-6`) | `gpt-5-mini` |
| `LLM_API_KEY` | required | — |
| `LLM_BASE_URL` | only for OpenAI-compatible endpoints (OpenRouter, Groq, Together, Azure, vLLM) | unset |
| `DB_PATH` | SQLite location | `./data/vendors.db` |
| `LOG_LEVEL` | | `INFO` |

The LLM layer is a Protocol + concrete adapters. Adding a new provider is one file; the pipeline doesn't change.

## Tech stack

FastAPI · SQLAlchemy 2.0 (async, aiosqlite) · Pydantic v2 · curl-cffi · Playwright · Jinja2 + HTMX + Tailwind v4 (browser CDN) · OpenAI / Anthropic SDKs.

One process, one repo, one deployment. SQLite-backed background worker spawned by FastAPI's lifespan handler — no Redis or external queue.

## Project layout

```
app/
├── main.py              # FastAPI app, lifespan, worker bootstrap
├── config.py            # pydantic-settings
├── db.py                # async engine, session, FK PRAGMA
├── models.py            # ORM models
├── schemas.py           # Pydantic — vendor I/O, LLM extraction targets, risk schemas
├── llm/                 # provider-agnostic LLM adapter (Protocol + OpenAI + Anthropic)
├── pipeline/            # discovery → ranking → selection → fetcher → extraction → analysis → runner
├── routes/              # pages, vendors CRUD, runs, reports, insights
├── templates/           # Jinja2
└── workers/runner.py    # background poller
```

## Trade-offs

Three things we knowingly accepted within the timebox; each has a clean upgrade path on the existing pipeline shape:

- **Edge bot-blocking on a few vendors** (Adobe blocks `robots.txt` outright; some trust portals 403 our user agent). curl-cffi's Chrome TLS impersonation handles the bulk, but vendors fronted by aggressive Akamai/Cloudflare configurations need more. The fetcher already has a fallback ladder (curl-cffi → Playwright); a third tier — residential proxy rotation (Bright Data, Oxylabs) or a paid anti-bot service — drops in behind the same interface. Out of scope for the assessment; in production we'd wire it on day two.
- **Sitemap-bound discovery.** Vendors whose privacy URL is a content-page id (Notion's `/Terms-and-Privacy-{hash}` is the canonical example) won't appear in the sitemap and so won't surface as a candidate. The next logical addition is on-page link parsing — fetch the homepage and footer, extract internal links, feed those back into the ranker. Same architecture, one new stage.
- **Operating-health from on-site evidence only.** Breaches, layoffs, and leadership changes are best sourced externally (HIBP, Layoffs.fyi, Crunchbase, news APIs). Today the LLM extracts what the vendor's own pages disclose, which is rarely the full picture. A scheduled enrichment job hitting external sources would persist alongside the existing extractions and merge into the same `signal_extraction` table.

## Cost

Each vendor = 1 LLM call for URL selection (low reasoning effort) + 1 for signal extraction (default effort). For the brief's 19-vendor set on `gpt-5-mini` (or `gpt-5.4-mini`), a full run lands well under $1. Selection is skipped entirely when heuristic ranking already returned ≤ 2 candidates per signal.

## Deployment

Ships with a `Dockerfile` based on Playwright's official Python image, so Chromium and its system libraries come pre-installed — no manual build hook required. Railway (and any Docker-aware platform) autodetects the Dockerfile and uses it.

To deploy on Railway:

1. Connect the repo (or `railway up`).
2. In the dashboard, set the env vars from `.env.example` (at minimum `LLM_API_KEY`).
3. Add a persistent volume mounted at `/app/data` so the SQLite database survives restarts.

`Procfile` is also retained for Heroku-style platforms that prefer Nixpacks builds, but those need an explicit `playwright install chromium --with-deps` step in the build phase.
