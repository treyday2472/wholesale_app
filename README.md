# DealBot / Wholesale App

Lightweight Flask + SQLAlchemy app for inbound wholesaling:
lead intake, quick property evaluation, and simple CRM endpoints.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # put your keys in .env
python run.py
```

Visit:
- `http://127.0.0.1:5000/` — quick evaluation form
- `http://127.0.0.1:5000/buyers` — buyers list
- `GET /api/health` — health check
- `GET /api/leads` — list leads (requires `X-API-Key`)
- `POST /api/leads` — create a lead (requires `X-API-Key`)
- `POST /api/evaluate` — compute base evaluation + exit strategies

### Auth for API
Send header: `X-API-Key: <value of API_KEY in .env>`

## Environment Variables
See `.env.example` for the full list of supported keys.

## Migrations (Recommended)
When your models stabilize, initialize Alembic/Flask-Migrate and create a
proper baseline migration.

See `docs/MIGRATIONS.md` for step-by-step.
