# DealBot API & Webhooks (Commit 1)

## Endpoints

- `GET /api/health` — check server
- `GET /api/leads` — list leads
- `GET /api/leads/<id>` — lead detail
- `POST /api/leads` — create lead (requires `X-API-Key` if API_KEY is set)
- `POST /api/webhooks/vonage/sms` — inbound SMS (JSON or form)
- `POST /api/webhooks/vonage/voice` — inbound call meta
- `POST /api/webhooks/google-ads` — log google-ads payload (requires API key)
- `POST /api/webhooks/facebook-leads` — log facebook-leads payload (requires API key)

## Config
Set `API_KEY` in `.env` to enforce token checks on sensitive endpoints.
