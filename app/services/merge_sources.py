from datetime import datetime

def _meta(source: str, as_of: str | None = None) -> dict:
    return {"source": source, "as_of": as_of or datetime.utcnow().date().isoformat()}

def merge_property(melissa: dict | None, zillow: dict | None):
    """
    Returns: facts, signals, meta, providers_raw
    facts precedence: Melissa > Zillow
    structure precedence (beds/baths/sqft): Melissa (public record) > Zillow
    signals: Zestimate, RentZestimate, photos/link from Zillow (side badges)
    """
    mel = melissa or {}
    zil = zillow or {}

    facts = {}
    meta  = {}

    # ---- Core facts (public record wins) ----
    for field in ("owner", "apn", "land_use", "year_built", "beds", "baths", "sqft",
                  "last_sale_date", "last_sale_price"):
        v_m = mel.get(field)
        v_z = zil.get(field)
        if v_m not in (None, "", []):
            facts[field] = v_m
            meta[field]  = _meta("Melissa", mel.get("as_of"))
        elif v_z not in (None, "", []):
            facts[field] = v_z
            meta[field]  = _meta("Zillow", zil.get("as_of"))

    # mortgages list (Melissa only, typically)
    if mel.get("mortgages"):
        facts["mortgages"] = mel["mortgages"]
        meta["mortgages"]  = _meta("Melissa", mel.get("as_of"))

    # ---- Signals (market-ish) ----
    signals = {
        "zestimate": zil.get("zestimate"),
        "rent_zestimate": zil.get("rent_zestimate"),
        "zillow_url": zil.get("url"),
        "zillow_photos": zil.get("photos") or [],
    }

    providers_raw = {
        "melissa": mel.get("raw"),
        "zillow":  zil.get("raw"),
    }

    return facts, signals, meta, providers_raw
