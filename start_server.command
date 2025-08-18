#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"; cd "$DIR" || exit 1
PYTHON=python3; command -v python3 >/dev/null 2>&1 || PYTHON=python
[ -d ".venv" ] || "$PYTHON" -m venv .venv || exit 1
# shellcheck disable=SC1091
source ".venv/bin/activate"
python -m pip install --upgrade pip
pip install -r requirements.txt
[ -f ".env" ] || { [ -f ".env.example" ] && cp ".env.example" ".env"; }
export FLASK_HOST=${FLASK_HOST:-127.0.0.1}
export FLASK_PORT=${FLASK_PORT:-5000}
python run.py
read -n 1 -s -r -p "Press any key to close..."
