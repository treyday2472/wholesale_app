# app/services/ai.py
import os, json
from datetime import datetime
from typing import List, Dict, Tuple

from openai import OpenAI   # pip install openai
client = OpenAI()

_USE_AI = bool(os.getenv("OPENAI_API_KEY"))
_MODEL  = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def _to_float(x):
    try: return float(x)
    except Exception: return None

def _days_ago(dstr):
    try:
        d = datetime.fromisoformat(str(dstr)[:10]).date()
        return (datetime.utcnow().date() - d).days
    except Exception:
        return None

def _norm_comp(c: Dict) -> Dict:
    return {
        "address": c.get("address") or c.get("_addr_line"),
        "saleDate": c.get("saleDate"),
        "price": c.get("price"),
        "beds": c.get("beds"),
        "baths": c.get("baths"),
        "sqft": c.get("sqft"),
        "yearBuilt": c.get("yearBuilt"),
        "distance": c.get("distance"),
        "docType": c.get("docType"),
        "propType": c.get("propType"),
    }

def score_comps_heuristic(subject: Dict, candidates: List[Dict]) -> List[Dict]:
    # … keep your existing heuristic here (unchanged) …
    from math import isfinite
    s_sqft = _to_float(subject.get("sqft"))
    out = []
    for c in candidates:
        dist = _to_float(c.get("distance")) or 9.9
        days = _days_ago(c.get("saleDate")) or 9999
        c_sqft = _to_float(c.get("sqft"))
        sqft_pen = abs((c_sqft - s_sqft)/s_sqft) if (s_sqft and c_sqft) else 0.5
        raw = (dist*2.0) + (days/90.0) + (sqft_pen*3.0)
        ai_score = 1.0 / (1.0 + raw)
        cc = dict(c); cc["ai_score"] = round(ai_score, 4)
        out.append(cc)
    out.sort(key=lambda r: r["ai_score"], reverse=True)
    return out

def choose_best_comps_with_ai(subject: Dict, candidates: List[Dict], k: int = 6) -> Tuple[List[Dict], str]:
    """
    Returns (picked_comps, notes). Falls back to heuristic on any error.
    """
    # Always have a safe fallback
    def _fallback(note: str):
        return score_comps_heuristic(subject, candidates)[:k], note

    if not _USE_AI:
        return _fallback("OpenAI not configured; using local heuristic.")

    try:
        # keep payload small for the model
        normed = [_norm_comp(c) for c in candidates[:30]]
        subj = {
            "address": subject.get("address"),
            "beds": subject.get("beds"),
            "baths": subject.get("baths"),
            "sqft": subject.get("sqft"),
            "yearBuilt": subject.get("yearBuilt"),
        }

        # Ask the model to pick the best K and return strict JSON
        system = (
            "You are an appraiser. Pick the K best comparable SALES for the subject. "
            "Prefer: recent date, close distance, similar sqft/year/beds/baths, normal arm's-length deed. "
            "Penalize missing price. Respond ONLY as JSON matching this schema: "
            '{"picks":[{"index":int,"ai_score":float,"reason":str}], "notes":str}. '
            "Indices refer to the provided candidates list."
        )

        prompt = {
            "k": k,
            "subject": subj,
            "candidates": normed
        }

        # Chat Completions in JSON mode (Responses API also works; either is fine).
        # See API reference for request shape. 
        resp = client.chat.completions.create(
            model=_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}
            ],
        )  # Docs/examples for Python SDK. :contentReference[oaicite:3]{index=3}

        content = resp.choices[0].message.content
        data = json.loads(content)

        picks = []
        for item in (data.get("picks") or []):
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < len(candidates):
                c = dict(candidates[idx])
                if "ai_score" in item: c["ai_score"] = float(item["ai_score"])
                if "reason"   in item: c["ai_reason"] = item["reason"]
                picks.append(c)
        if not picks:
            return _fallback("OpenAI returned no picks; using heuristic.")
        return picks[:k], f"OpenAI model '{_MODEL}' ranking."

    except Exception as e:
        # Try Flask logger if available, else print
        try:
            from flask import current_app
            current_app.logger.exception("OpenAI ranking failed")
        except Exception:
            import traceback, sys
            print("OpenAI ranking failed:", repr(e), file=sys.stderr)
            traceback.print_exc()

        return _fallback("OpenAI call failed; using heuristic.")
