# Database Migrations (Flask-Migrate / Alembic)

> You currently have a working SQLite `app.db`. For team/dev/prod, use proper migrations.

## One-time setup
1. Install deps (already in `requirements.txt`): `Flask-Migrate`, `alembic`
2. From the project root:
   ```bash
   export FLASK_APP=run.py  # Windows: set FLASK_APP=run.py
   flask db init
   flask db migrate -m "initial schema"
   flask db upgrade
   ```

## Ongoing changes
Whenever models change:
```bash
flask db migrate -m "explain the change"
flask db upgrade
```

## Notes
- Keep `migrations/` in git so others can reproduce schema.
- For SQLite, `flask db downgrade` may be limited; prefer forward-only changes in dev.
