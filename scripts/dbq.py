# scripts/dbq.py
"""
Quick DB migration runner (migrate -> upgrade) for Flask-Migrate.
Usage:
    python scripts/dbq.py -m "add sf_lead_id to Lead"
Options:
    --init-if-needed : If migrations/ isn't initialized, run `flask db init` first.
Env:
    FLASK_APP will be set to "app:create_app" if not already set.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # .../Wholesale app
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

def run(cmd: list[str]) -> None:
    print(">>>", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), shell=False)
    if proc.returncode != 0:
        sys.exit(proc.returncode)

def main():
    parser = argparse.ArgumentParser(description="Quick Alembic migrate+upgrade")
    parser.add_argument("-m", "--message", default="quick migration",
                        help="Migration message for Alembic revision")
    parser.add_argument("--init-if-needed", action="store_true",
                        help="Run `flask db init` if migrations/ is missing")
    args = parser.parse_args()

    # Ensure we're at project root
    os.chdir(PROJECT_ROOT)

    # Point Flask CLI at your app factory
    os.environ.setdefault("FLASK_APP", "app:create_app")

    # Optionally initialize migrations if not present
    if args.init_if_needed and not (MIGRATIONS_DIR / "env.py").exists():
        print("migrations/ not found. Initializing with `flask db init`…")
        run(["flask", "db", "init"])

    # Create (auto-generate) a migration script
    run(["flask", "db", "migrate", "-m", args.message])

    # Apply it
    run(["flask", "db", "upgrade"])

    print("✅ Done.")

if __name__ == "__main__":
    main()
