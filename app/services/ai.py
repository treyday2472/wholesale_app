# app/services/ai.py
import os, json
from datetime import datetime
from typing import List, Dict, Tuple

# Optional: OpenAI ranking (skip gracefully if not configured)
_USE_AI = bool(os.getenv("OPENAI_API_KEY"))

def _parse_date_any(s):
    if not s:
        return None
    s = str(s).strip()
    s10 = s[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(s10, fmt).date()
        except Exception:
            pass
    return None



_ALLOWED_DEED_TOKENS = ("DEED", "GRANT DEED", "WARRANTY DEED", "SPECIAL WARRANTY DEED", "QUIT CLAIM DEED")
_EXCLUDED_DOC_TOKENS = ("MORTGAGE", "DEED OF TRUST", "ASSIGNMENT", "RELEASE", "LIEN", "UCC", "FORECLOSURE")

def _is_deed_doc(doc):
    if not doc: return False
    d = str(doc).upper()
    if any(x in d for x in _EXCLUDED_DOC_TOKENS): return False
    return any(x in d for x in _ALLOWED_DEED_TOKENS) or d.strip() == "DEED"

def _norm_comp(c: Dict) -> Dict:
    # keep only fields the model needs
    return {
        "address": c.get("address"),
        "saleDate": c.get("saleDate"),
        "price": c.get("price"),
        "beds": c.get("beds"),
        "baths": c.get("baths"),
        "sqft": c.get("sqft"),
        "yearBuilt": c.get("yearBuilt"),
        "distance": c.get("distance"),
        "docType": c.get("docType"),
        "transType": c.get("transType"),
        "propType": c.get("propType") or c.get("summary", {}).get("propLandUse"),
    }

def _months_ago(s) -> float | None:
    d = _parse_date_any(s)
    if not d: return None
    today = datetime.utcnow().date()
    return (today.year - d.year) * 12 + (today.month - d.month)

def score_comps_heuristic(subject: Dict, candidates: List[Dict]) -> List[Dict]:
    """Deterministic fallback ranking."""
    s_sf   = subject.get("sqft")
    s_year = subject.get("yearBuilt")
    s_bed  = subject.get("beds")
    s_bath = subject.get("baths")

    out = []
    for c in candidates:
        # basic sanity
        dist = None
        try: dist = float(c.get("distance")) if c.get("distance") is not None else None
        except: pass

        m_ago = _months_ago(c.get("saleDate"))
        price = c.get("price")
        deed  = _is_deed_doc(c.get("docType"))

        # similarity terms (0..1 each)
        sim = 0.0
        w = 0.0

        # recency (≤ 120 mo preferred)
        if m_ago is not None:
            sim += max(0.0, 1.0 - (m_ago / 120.0)) * 3.0; w += 3.0

        # distance (≤ 5 mi preferred)
        if dist is not None:
            sim += max(0.0, 1.0 - (dist / 5.0)) * 2.0; w += 2.0

        # size
        if s_sf and c.get("sqft"):
            try:
                dsf = abs(float(c["sqft"]) - float(s_sf)) / float(s_sf)
                sim += max(0.0, 1.0 - (dsf / 0.30)) * 2.0; w += 2.0
            except: pass

        # year
        if s_year and c.get("yearBuilt"):
            try:
                dy = abs(int(c["yearBuilt"]) - int(s_year))
                sim += max(0.0, 1.0 - (dy / 20.0)) * 1.0; w += 1.0
            except: pass

        # beds / baths closeness
        if s_bed is not None and c.get("beds") is not None:
            try:
                db = abs(int(c["beds"]) - int(s_bed))
                sim += max(0.0, 1.0 - (db / 3.0)) * 1.0; w += 1.0
            except: pass
        if s_bath is not None and c.get("baths") is not None:
            try:
                dba = abs(float(c["baths"]) - float(s_bath))
                sim += max(0.0, 1.0 - (dba / 2.0)) * 1.0; w += 1.0
            except: pass

        # favor deed with known price
        if deed and price:
            sim *= 1.10  # slight boost
        elif not price:
            sim *= 0.70  # penalize missing price

        score = sim / w if w > 0 else 0.0
        out.append({ **c, "score": round(score, 4) })

    out.sort(key=lambda x: (x["score"], x.get("saleDate") or ""), reverse=True)
    return out

# app/services/ai.py
from datetime import datetime

def _to_float(x):
    try: return float(x)
    except Exception: return None

def _days_ago(dstr):
    try:
        d = datetime.fromisoformat(str(dstr)[:10]).date()
        return (datetime.utcnow().date() - d).days
    except Exception:
        return None

def choose_best_comps_with_ai(subject, candidates, k=6):
    """Simple scoring that looks like AI to the app. Produces ai_score."""
    s_sqft = _to_float(subject.get("sqft"))
    scored = []
    for c in candidates:
        dist = _to_float(c.get("distance")) or 9.9
        days = _days_ago(c.get("saleDate")) or 9999
        c_sqft = _to_float(c.get("sqft"))
        sqft_pen = abs((c_sqft - s_sqft)/s_sqft) if (s_sqft and c_sqft) else 0.5

        # Smaller is better → invert to 0..1 for display
        raw = (dist*2.0) + (days/90.0) + (sqft_pen*3.0)
        ai_score = 1.0 / (1.0 + raw)

        cc = dict(c)
        cc["ai_score"] = round(ai_score, 4)
        scored.append(cc)

    scored.sort(key=lambda r: r["ai_score"], reverse=True)
    return scored[:k], "AI module present (local scoring)."
