# API Security Checklist (Dev)

- [x] Require `X-API-Key` for all write endpoints (`/api/leads`, `/api/evaluate`).
- [ ] Add simple rate-limiting (see `app/utils/ratelimit.py`). Apply to public endpoints.
- [ ] Validate request bodies (see `app/utils/validation.py`). Fail fast on bad input.
- [ ] Centralize HTTP client timeouts and error mapping.
- [ ] Avoid leaking stack traces; return `{ "error": "<code>" }`.
- [ ] Log errors with request id/ip for later tracing.
- [ ] Put real secrets in `.env` only (never commit). Share a `.env.example`.
