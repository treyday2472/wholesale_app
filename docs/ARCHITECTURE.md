
# Architecture & Code Map

This app is a lightweight Flask + SQLAlchemy wholesaling tool. Below is a map of
every major file and what it does, so you can follow the flow and troubleshoot fast.

## Top-Level
- `run.py` — loads `.env`, creates the Flask app via `app.create_app()`, runs the server.
- `.env` / `.env.example` — environment variables (API keys, DB URL, etc.).
- `config.py` — default settings pulled by `create_app()` (DB URL, API keys, etc.).
- `requirements.txt` — Python dependencies.

## `app/` package
- `__init__.py` — **App Factory**: initializes `db`, `csrf`, `migrate`, registers blueprints, and sets Jinja helpers.
- `models.py` — SQLAlchemy models:  - `Property` — address, zpid, structure facts, location, lead-status, and `raw_json` (legacy).
  - `Lead`, `Buyer` — CRM entities.
  - `LeadEvent` — timeline entries for leads (notes/calls).
  - `PropertySourceData` — **NEW**: stores raw provider payloads by `source` (zillow/melissa) and `subtype` (LookupProperty/LookupDeeds), JSON + timestamps.
- `routes.py` — UI routes and flow:  - `/` property evaluator (quick form) -> `/eval` results.  - `/properties` list/create; `/properties/<id>` detail (now passes `zillow`, `melissa_lookup`, `melissa_deeds`).  - `/properties/<id>/enrich-melissa` pulls Melissa data and stores in `PropertySourceData`.  - `/admin/table/<table>` **NEW**: quick DB viewer for Property/Lead/Buyer/PropertySourceData.
- `forms.py` — WTForms for lead & buyer multi-step flows.
- `templates/` — Jinja pages.  - `property_detail.html` — **UPDATED**: distinct sections for **Zillow** and **Melissa** with raw payload viewers.  - `admin_table.html` — **NEW**: spreadsheet-like DB viewer.

## `app/services/`
- `zillow_client.py` — Zillow API helpers: search by address, details by zpid, normalized snapshot builders.
- `melissa_client.py` — Melissa API helpers: `lookup_property`, `lookup_deeds` and normalizer.  (Tests like `melissa_merge_test.py` were for payload exploration. Now persisted to DB.)
- `merge_sources.py` — merges/normalizes Zillow + Melissa into a single `snapshot` dict for the UI.
- `amortization.py` — finance helpers for mortgage balance estimates.
- `http_client.py` — **(recommended)** central GET with timeouts + error mapping (if not already used).

## `api/` package
- `auth.py` — `require_token()` for `/api/*` endpoints using `X-API-Key`.
- `routes.py` — REST endpoints (`/api/health`, `/api/leads`, `/api/evaluate`, etc.).

## Data Flow (Property Detail)
1. `/properties/<id>` loads the record, builds a **Zillow snapshot** via `build_snapshot_for_property()`.2. It also loads **Melissa** latest payloads from `PropertySourceData` (LookupProperty/LookupDeeds).3. The template `property_detail.html` shows two clear sections: **Zillow** and **Melissa**.4. Click **Enrich with Melissa** (POST `/properties/<id>/enrich-melissa`) to fetch/store fresh payloads.

## Troubleshooting
- Use `/admin/table/PropertySourceData?limit=50` to eyeball stored JSON.
- If Zillow/Melissa calls fail, check `.env` keys and logs. Consider using `http_client.safe_get()` for timeouts and clean error mapping.

