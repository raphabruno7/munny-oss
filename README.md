# Munny

Personal Open Banking aggregator with an AI financial agent.

Aggregates bank accounts through **Enable Banking** (PSD2, redirect-based SCA
consent), stores history in SQLite, classifies transactions with a hybrid
rule + LLM pipeline, and exposes everything as a **remote MCP server** so an AI
assistant can query the data directly. A server-rendered dashboard and a weekly
pt-PT insight report sit on top.

## Architecture

```
Enable Banking (PSD2)  ──►  app/sources/enable_banking.py   JWT RS256, consent flow, paginated sync
                            app/sync.py                     per-ASPSP daily request quota
                                    │
                                    ▼
                            SQLite  (accounts, bank_connections, transactions)
                            idempotent via UNIQUE(source, external_id)
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        ▼                           ▼                           ▼
app/categorize.py            app/mcp_server.py            app/dashboard.py
keyword rules (categories.yaml)   Streamable HTTP /mcp    FastAPI + Jinja2 + HTMX
+ batched LLM fallback (Gemini)   bearer auth             balance cards, filterable
manual overrides survive re-sync  read + write tools      ledger, spend-by-category
        │
        ▼
app/report.py  ──►  weekly job (APScheduler): insights → Gemini narration (pt-PT) → Resend email
```

## Components

- **`app/sources/enable_banking.py`** — RS256 JWT auth, PSD2 redirect consent,
  balances and transactions with continuation-key pagination.
- **`app/categorize.py`** — keyword rules first (`categories.yaml`), batched Gemini
  fallback for the rest, with a short rationale per transaction. Manual corrections
  are marked `category_source = 'manual'` and survive a re-sync.
- **`app/mcp_server.py`** — MCP server (Streamable HTTP) mounted at `/mcp`, bearer
  auth, fails closed on an empty token. Tools: `weekly_summary`,
  `recurring_payments`, `upcoming_obligations`, `save_weekly_report`.
- **`app/dashboard.py`** — server-rendered pages in the same FastAPI app
  (Jinja2 + HTMX + Chart.js via CDN, no build step). Cookie session signed with
  HMAC-SHA256 (stdlib).
- **`app/insights.py`** — pure functions: weekly summary vs. prior week and 4-week
  average, recurring-payment detection, upcoming-obligation reserve funding.
- **`app/report.py`** — weekly APScheduler job: assembles insights, has Gemini
  narrate them in pt-PT, stores the report and emails it via Resend.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill ENABLE_BANKING_APPLICATION_ID, ENABLE_BANKING_PRIVATE_KEY_B64,
                       # MCP_BEARER_TOKEN, GEMINI_API_KEY
uvicorn app.main:app --reload
```

```bash
pytest
```

## Deploy

`Dockerfile` (`python:3.12-slim`, `uvicorn` on `$PORT`). APScheduler runs inside the
app lifespan and syncs every 6h — single replica only. Runs on Railway.

## Compliance

PSD2 / SCA consent flow, per-ASPSP request quotas, no credentials in the repo
(`secrets/` and `.env` are gitignored).
