# VendorIntelligence

Vendor intelligence reports for SaaS procurement. Given a list of vendor domains, produces a per-vendor report covering security posture, sub-processors, privacy & data residency, pricing signals, ownership, and operating-health flags — plus a composite renewal-risk score with attributed components.

## Status

Early scaffold. Pipeline stages, UI, and report rendering arrive over the next commits — see `dev/plan.md` for the build order.

## Local development

```bash
python -m venv .venv
.venv\Scripts\activate     # Windows
# source .venv/bin/activate  # macOS/Linux

pip install -r requirements.txt
cp .env.example .env       # fill in LLM_API_KEY when the extraction stage lands

uvicorn app.main:app --reload
```

App runs on `http://localhost:8000`. Health probe at `/healthz`.

## Deployment

Single FastAPI service. `Procfile` is set up for Railway / Heroku-style platforms. SQLite database file lives at `DB_PATH` (default `./data/vendors.db`) — point this at a mounted volume in production.
