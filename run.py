# run.py
from dotenv import load_dotenv
from pathlib import Path
import os

# Pick the file you are actually using
root = Path(__file__).resolve().parent
env_path = root / ".env"              # ← rename .env.txt to .env (recommended)
# env_path = root / ".env.txt"        # ← or point to .env.txt if you prefer

load_dotenv(dotenv_path=str(env_path), override=True)

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)

