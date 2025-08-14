from .melissa_client import lookup_property, lookup_deeds, lookup_homes_by_owner, normalize_property_record, MelissaHttpError
from .zillow_client import evaluate_address_with_marketdata  # keep for signals
from .merge_sources import merge_property
from .amortization import estimate_balance
from flask import current_app

def evaluate_property(address: str, full_address: str | None, lat: float | None, lng: float | None,
                      google_key: str = "", rapid_key: str = "") -> dict:
    """
    Returns dict with: full_address, lat, lng, facts, signals, meta, providers
    """
    # 1) Normalize address via Melissa
    norm = normalize_property_record(full_address or address)
    street = norm.get("street") or address
    city   = norm.get("city") or ""
    state  = norm.get("state") or ""
    postal = norm.get("postal_code") or ""

    # 2) Melissa property facts
    mel_facts = lookup_property(street, city, state, postal)
    mel_facts["as_of"] = norm.get("as_of")

    # 3) Zillow signals (optional; keep if you still want zestimate/rent/photos)
    z_sig = {}
    try:
        z = evaluate_address_with_marketdata(norm["full_address"],
                                             current_app.config.get("RAPIDAPI_KEY", ""),
                                             current_app.config.get("ZILLOW_HOST", "zillow-com1.p.rapidapi.com"))
        z_sig = {
            "zestimate": (z.get("home") or {}).get("zestimate"),
            "rent_zestimate": (z.get("home") or {}).get("rentZestimate"),
            "url": (z.get("home") or {}).get("zillowUrl"),
            "photos": (z.get("home") or {}).get("photos") or [],
            "raw": z
        }
        z_sig["as_of"] = z.get("asOf") or z.get("date")
    except Exception:
        z_sig = {}

    facts, signals, meta, providers = merge_property(mel_facts, z_sig)

    # 4) Try estimating unpaid balance for each mortgage (if we have enough fields)
    ests = []
    for m in facts.get("mortgages", []):
        bal = estimate_balance(
            orig_amount = _to_float(m.get("orig_amount")),
            rate_pct    = _to_float(m.get("rate")),
            orig_date   = m.get("orig_date"),
            term_months = int(m.get("term_months") or 360),
        )
        if bal is not None:
            m["estimated_balance"] = round(bal, 2)

    return {
        "full_address": norm.get("full_address"),
        "lat": norm.get("lat") if norm.get("lat") else lat,
        "lng": norm.get("lng") if norm.get("lng") else lng,
        "facts": facts,
        "signals": signals,
        "meta": meta,
        "providers": providers,
    }

def _to_float(x):
    try:
        if x in (None, "", "None"): return None
        return float(str(x).replace(",","").replace("$",""))
    except Exception:
        return None
