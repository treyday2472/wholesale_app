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

import builtins, traceback

_real_print = builtins.print
def _spy_print(*args, **kwargs):
    # Only dump a stack when it looks like that Zillow dict
    if args and isinstance(args[0], dict) and 'zpid' in args[0]:
        _real_print("\n=== PRINT CALLED: STACK TRACE ===")
        traceback.print_stack(limit=25)
        _real_print("=== END STACK TRACE ===\n")
    return _real_print(*args, **kwargs)

builtins.print = _spy_print


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)

