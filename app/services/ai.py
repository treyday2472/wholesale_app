# app/services/ai.py
import os, json, math, time, logging, re
from datetime import datetime
from statistics import median
from typing import List, Dict, Tuple, Optional
from openai import OpenAI


_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # fast default
_OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "8")) # seconds

_oai: Optional[OpenAI] = None
def _client() -> Optional[OpenAI]:
    """Lazy client with a short timeout so we fail quickly."""
    global _oai
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    if _oai is None:
        _oai = OpenAI(api_key=key, timeout=_OPENAI_TIMEOUT)
    return _oai

def _chat_kwargs() -> dict:
    """JSON-mode for Chat Completions; fixed deterministic output."""
    return {"temperature": 0, "response_format": {"type": "json_object"}}


# -----------------------------
# Small helpers
# -----------------------------
_money_re = re.compile(r"[^0-9.\-]+")

def _to_float(x):
    try: return float(x)
    except Exception: return None

def _days_ago(dstr):
    try:
        d = datetime.fromisoformat(str(dstr)[:10]).date()
        return (datetime.utcnow().date() - d).days
    except Exception:
        return None

def _ppsf(price, sqft) -> Optional[float]:
    p = _to_float(price); s = _to_float(sqft)
    if p and s and s > 0: return p / s
    return None

def _num(x) -> Optional[float]:
    if isinstance(x, (int, float)): return float(x)
    if x is None: return None
    s = _money_re.sub("", str(x))
    try: return float(s)
    except Exception: return None

def _int_idx(x) -> Optional[int]:
    n = _num(x)
    try: return int(n) if n is not None else None
    except Exception: return None

# -----------------------------
# Heuristic comp scoring
# -----------------------------
def score_comps_heuristic(subject: Dict, candidates: List[Dict]) -> List[Dict]:
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

# -----------------------------
# AI comps selection (Chat Completions)
# -----------------------------
def _compact(c: Dict) -> Dict:
    return {
        "address":   c.get("address") or c.get("_addr_line"),
        "saleDate":  c.get("saleDate"),
        "price":     c.get("price"),
        "beds":      c.get("beds"),
        "baths":     c.get("baths"),
        "sqft":      c.get("sqft"),
        "yearBuilt": c.get("yearBuilt"),
        "distance":  c.get("distance"),
        "propType":  c.get("propertyType") or c.get("propclass") or c.get("proptype"),
    }

def choose_best_comps_with_ai(subject: Dict, candidates: List[Dict], k: int = 6) -> Tuple[List[Dict], str]:
    cli = _client()
    if not cli:
        picks = score_comps_heuristic(subject, candidates)[:k]
        return picks, "OpenAI not configured → heuristic."

    normed = [_compact(c) for c in candidates[:30]]
    subj = {key: subject.get(key) for key in ("address","beds","baths","sqft","yearBuilt")}

    system = (
        "You are an appraiser. Pick the K best comparable CLOSED SALES.\n"
        "Prefer: recent (≤6 mo), close (≤0.5 mi), ±15% sqft, ±5 yrs, similar beds/baths/type; penalize missing price.\n"
        "Return JSON ONLY in this schema: "
        '{"picks":[{"index":int,"ai_score":float,"reason":str}],"notes":str}.\n'
        "Return EXACTLY K items in picks. If there are <K priced comps, fill the remaining with the next best matches."
    )
    prompt = {"k": k, "subject": subj, "candidates": normed}

    raw = ""
    try:
        resp = cli.chat.completions.create(
            model=_OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            **_chat_kwargs(),
        )
        raw = resp.choices[0].message.content or ""
        data = json.loads(raw)

        picks: List[Dict] = []
        for item in (data.get("picks") or []):
            idx = _int_idx(item.get("index") if "index" in item else item.get("idx"))
            if idx is None or not (0 <= idx < len(candidates)):
                continue
            c = dict(candidates[idx])
            s = _num(item.get("ai_score"))
            if s is not None: c["ai_score"] = float(s)
            if "reason" in item: c["ai_reason"] = str(item["reason"])[:300]
            picks.append(c)

        if not picks:
            raise ValueError("Empty picks.")
        return picks[:k], f"OpenAI model '{_OPENAI_MODEL}' ranking."

    except Exception as e:
        logging.warning("OpenAI comps failed: %s; raw=%r", e, raw[:400])
        picks = score_comps_heuristic(subject, candidates)[:k]
        return picks, "OpenAI call failed; using heuristic."

# -----------------------------
# ARV (local weighted median + optional AI refine)
# -----------------------------
def _weight_for_comp(subject: Dict, c: Dict) -> float:
    s_sqft = _to_float(subject.get("sqft"))
    c_sqft = _to_float(c.get("sqft"))
    dist   = _to_float(c.get("distance"))
    days   = _days_ago(c.get("saleDate"))

    w = 1.0
    if days is not None:  w *= math.exp(-(days / 180.0))  # recency
    if dist is not None:  w *= math.exp(-(dist / 0.5))    # locality
    if s_sqft and c_sqft:
        rel = abs(c_sqft - s_sqft) / s_sqft
        w  *= math.exp(-(rel / 0.15))                     # size similarity
    if c.get("price"):   w *= 1.05                         # known closed price
    return w

def _local_arv(subject: Dict, comps: List[Dict], k: int = 6) -> Tuple[Dict, str]:
    ranked = sorted(list(comps), key=lambda r: (r.get("ai_score") or r.get("score") or 0.0), reverse=True)

    chosen, rows = [], []
    for i, c in enumerate(ranked):
        if len(chosen) >= k: break
        ppsf = _ppsf(c.get("price"), c.get("sqft"))
        if ppsf is None and subject.get("sqft") and c.get("price"):
            s_sqft = _to_float(subject.get("sqft"))
            if s_sqft and s_sqft > 0:
                ppsf = _to_float(c.get("price")) / s_sqft
        if ppsf is None: continue
        w = _weight_for_comp(subject, c)
        rows.append((i, ppsf, w))
        chosen.append(i)

    if not rows:
        return ({"arv": None, "low": None, "high": None, "used": []},
                "No priced comps → ARV unavailable.")

    expanded = []
    for i, ppsf, w in rows:
        reps = max(1, min(50, int(round(w * 10))))
        expanded.extend([(i, ppsf)] * reps)

    ppsf_vals = [v for _, v in expanded]
    ppsf_vals.sort()
    mid = median(ppsf_vals)
    q1  = ppsf_vals[int(0.25 * (len(ppsf_vals)-1))]
    q3  = ppsf_vals[int(0.75 * (len(ppsf_vals)-1))]

    s_sqft = _to_float(subject.get("sqft"))
    if not s_sqft:
        return ({"arv": None, "low": None, "high": None, "used": chosen[:k]},
                "Subject missing sqft → ARV per-sf only.")

    arv  = mid * s_sqft
    low  = q1  * s_sqft
    high = q3  * s_sqft
    pack = {"arv": round(arv, 0), "low": round(low, 0), "high": round(high, 0), "used": chosen[:k]}
    return pack, "Local ARV from weighted median $/sf."

def suggest_arv(subject: Dict, comps: List[Dict], k: int = 6) -> Tuple[Dict, str]:
    local_pack, local_note = _local_arv(subject, comps, k=k)

    cli = _client()
    if not cli:
        return local_pack, f"{local_note} (No OpenAI key.)"

    rows = []
    for i, c in enumerate(comps[:20]):
        rows.append({
            "index": i,
            "address":  c.get("address") or c.get("_addr_line"),
            "saleDate": c.get("saleDate"),
            "price":    c.get("price"),
            "sqft":     c.get("sqft"),
            "beds":     c.get("beds"),
            "baths":    c.get("baths"),
            "yearBuilt":c.get("yearBuilt"),
            "distance": c.get("distance"),
        })

    system = (
        "You are a residential appraisal assistant. Estimate After Repair Value (ARV) from CLOSED SALE comps.\n"
        "Prefer recent (≤6 mo), close (≤0.5mi), ±15% size, similar year, and real closed prices.\n"
        "Respond with JSON ONLY like "
        '{"arv":312000,"low":295000,"high":330000,"used":[0,3,5],"why":"one sentence"}.\n'
        "Use plain numbers for arv/low/high (no $, commas, or quotes). "
        "Always return values; if few priced comps, infer conservatively from nearest valid comps."
    )

    user = {
        "subject": {k: subject.get(k) for k in ("address","beds","baths","sqft","yearBuilt")},
        "local_baseline": local_pack,
        "candidates_top20": rows,
        "k": k
    }

    raw = ""
    last_err = None
    for attempt in range(2):
        try:
            resp = cli.chat.completions.create(
                model=_OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
                **_chat_kwargs(),
            )
            raw = resp.choices[0].message.content or ""
            logging.info("ARV raw output: %r", raw[:400])

            data = json.loads(raw)
            arv  = _num(data.get("arv"))
            low  = _num(data.get("low"))
            high = _num(data.get("high"))
            used = []
            for u in (data.get("used") or []):
                j = _int_idx(u)
                if j is not None and 0 <= j < len(comps):
                    used.append(j)
            used = used[:k]
            why = data.get("why")

            if arv is not None:
                pack = {
                    "arv":  round(arv,  0),
                    "low":  round(low,  0) if low  is not None else local_pack.get("low"),
                    "high": round(high, 0) if high is not None else local_pack.get("high"),
                    "used": used or local_pack.get("used", []),
                }
                note = f"OpenAI refined from local baseline. {why}" if why else "OpenAI refined from local baseline."
                return pack, note

            raise ValueError("Model did not return numeric ARV.")
        except Exception as e:
            last_err = e
            logging.warning("OpenAI ARV attempt %d failed: %s; raw=%r", attempt+1, e, raw[:400])
            time.sleep(0.8)

    return local_pack, f"{local_note} (OpenAI call failed → heuristic ARV. {type(last_err).__name__}: {last_err})"
