# app/services/enrichers.py
from __future__ import annotations
import os
import re
import requests
from typing import Any, Dict, Optional, Tuple, List, Union

# ============================================================================
# Small helpers
# ============================================================================

def _cfg(name: str, default: str = "") -> str:
    val = os.getenv(name, default)
    return val.strip() if isinstance(val, str) else default

def _ok(v) -> bool:
    return v not in (None, "", [], {})

def _first(*vals):
    for v in vals:
        if _ok(v):
            return v
    return None

def _as_int(v) -> Optional[int]:
    try:
        return int(round(float(v)))
    except Exception:
        return None

def _as_float(v) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None

def _strip(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    return s.strip() or None

def _property_kind_from_text(txt: Optional[str]) -> Optional[str]:
    """Normalize property type labels across sources."""
    t = (txt or "").strip().lower()
    if not t:
        return None
    # Common buckets we care about in logic
    if any(k in t for k in ("land", "lot", "vacant land")):
        return "Land"
    if any(k in t for k in ("duplex", "triplex", "fourplex", "multifamily", "multi family", "multi-family", "apartment building")):
        return "Multifamily"
    if any(k in t for k in ("condo", "townhome", "townhouse")):
        return "SFR"  # treat as SFR-ish for offer logic; customize if needed
    if any(k in t for k in ("single family", "single-family", "sfr", "house", "detached")):
        return "SFR"
    # Default
    return txt  # keep raw if unknown; UI can still show it

# ============================================================================
# Zillow (RapidAPI) — basic facts
# Expected env:
#   RAPIDAPI_KEY
#   ZILLOW_HOST   e.g. "zillow56.p.rapidapi.com"
# Endpoints used (typical on RapidAPI):
#   GET /propertyExtendedSearch?location=...
#   GET /property?zpid=...
# ============================================================================

def _rapid_headers() -> Dict[str, str]:
    key = _cfg("RAPIDAPI_KEY")
    if not key:
        return {}
    return {
        "x-rapidapi-key": key,
        "x-rapidapi-host": _cfg("ZILLOW_HOST"),
    }

def zillow_find_zpid_by_address(address: str) -> Optional[str]:
    """Use propertyExtendedSearch to resolve zpid."""
    host = _cfg("ZILLOW_HOST")
    headers = _rapid_headers()
    if not host or not headers or not address:
        return None
    url = f"https://{host}/propertyExtendedSearch"
    try:
        r = requests.get(url, params={"location": address}, headers=headers, timeout=(8, 20))
        if not r.ok:
            return None
        j = r.json()
        items = j.get("props") or j.get("results") or j.get("data") or []
        if not isinstance(items, list) or not items:
            # Some implementations return an object with single zpid
            single = j.get("zpid")
            return str(single) if single else None
        # Best-effort: first item with zpid
        for it in items:
            zpid = it.get("zpid")
            if zpid:
                return str(zpid)
    except requests.RequestException:
        return None
    return None

def zillow_property_by_zpid(zpid: Union[str, int]) -> Optional[Dict[str, Any]]:
    """Fetch full details from Zillow 'property' endpoint."""
    host = _cfg("ZILLOW_HOST")
    headers = _rapid_headers()
    if not host or not headers or not zpid:
        return None
    url = f"https://{host}/property"
    try:
        r = requests.get(url, params={"zpid": zpid}, headers=headers, timeout=(8, 20))
        if not r.ok:
            return None
        j = r.json()
        # RapidAPI wrappers vary; try a few shapes:
        data = j.get("property") or j.get("data") or j
        if isinstance(data, dict):
            return data
    except requests.RequestException:
        return None
    return None

def parse_zillow_details(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Map Zillow raw to our canonical fields."""
    if not isinstance(raw, dict):
        return {}
    rf = raw.get("resoFacts") or {}
    addr = raw.get("address") or {}
    # Some wrappers put everything top-level
    full_address = _first(
        raw.get("streetAddress"),
        rf.get("streetAddress"),
        addr.get("streetAddress"),
        raw.get("address"),
        raw.get("fullAddress"),
    )
    city  = _first(raw.get("city"), rf.get("city"), addr.get("city"))
    state = _first(raw.get("state"), rf.get("state"), addr.get("state"))
    zipc  = _first(raw.get("zipcode"), raw.get("postalCode"), rf.get("zipcode"), addr.get("zipcode"))
    kind  = _first(raw.get("homeType"), rf.get("propertyType"), raw.get("propertyType"))
    kind  = _property_kind_from_text(kind)

    beds  = _as_float(_first(raw.get("bedrooms"), rf.get("bedrooms")))
    baths = _as_float(_first(raw.get("bathrooms"), rf.get("bathrooms")))
    sqft  = _as_int(_first(raw.get("livingArea"), rf.get("livingArea")))
    lot   = _as_int(_first(raw.get("lotArea"), rf.get("lotArea")))
    year  = _strip(_first(raw.get("yearBuilt"), rf.get("yearBuilt")))

    lat   = _as_float(_first(raw.get("latitude"),  raw.get("lat"),  (addr or {}).get("lat")))
    lng   = _as_float(_first(raw.get("longitude"), raw.get("lng"),  (addr or {}).get("lng")))

    zestimate = _as_int(_first(raw.get("zestimate"), raw.get("price"), rf.get("zestimate")))
    rent_zest = _as_int(_first(raw.get("rentZestimate"), rf.get("rentZestimate")))

    return {
        "provider": "zillow",
        "zpid": _strip(_first(raw.get("zpid"), raw.get("id"))),
        "fullAddress": _strip(full_address),
        "city": _strip(city), "state": _strip(state), "zipcode": _strip(zipc),
        "lat": lat, "lng": lng,
        "beds": beds, "baths": baths, "sqft": sqft, "lotSize": lot, "yearBuilt": year,
        "propertyType": kind,
        "zestimate": zestimate, "rentZestimate": rent_zest,
        "raw": raw,
    }

# ============================================================================
# Melissa — fallback for core facts + AVM if available
# Expected env:
#   MELISSA_API_KEY or MELISSA_KEY
# Typical REST: /globaladdress or /property endpoints vary by plan.
# Below uses a generic US Property endpoint shape; adjust params to your plan.
# ============================================================================

def melissa_property_by_address(address: str, city: Optional[str], state: Optional[str], zipcode: Optional[str]) -> Optional[Dict[str, Any]]:
    key = _cfg("MELISSA_API_KEY") or _cfg("MELISSA_KEY")
    if not key or not address:
        return None
    # Example endpoint — you may need to swap to your exact plan’s URL & params.
    url = "https://property.melissadata.net/v4/web/LookupProperty"
    params = {
        "id": key,
        "ff": "True",  # formatted fields
        "cols": "GrpAll",  # grab everything we can
        "a1": address,
    }
    if city: params["city"] = city
    if state: params["state"] = state
    if zipcode: params["postal"] = zipcode
    try:
        r = requests.get(url, params=params, timeout=(8, 20))
        if not r.ok:
            return None
        j = r.json()
        # Melissa returns Records list
        recs = j.get("Records") or j.get("Property") or []
        if isinstance(recs, list) and recs:
            return recs[0]
        if isinstance(recs, dict):
            return recs
    except requests.RequestException:
        return None
    return None

def parse_melissa_details(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    addr1 = _first(raw.get("AddressLine1"), raw.get("Address"))
    city  = raw.get("City")
    state = raw.get("State")
    zipc  = _first(raw.get("PostalCode"), raw.get("Zip"))

    # Beds/baths/sqft fields differ by plan
    beds  = _as_float(_first(raw.get("Bedrooms"), raw.get("BedroomsCount")))
    baths = _as_float(_first(raw.get("Bathrooms"), raw.get("BathroomsCount")))
    sqft  = _as_int(_first(raw.get("BuildingArea"), raw.get("SquareFeet")))
    lot   = _as_int(_first(raw.get("LotSize"), raw.get("LotAcreage")))
    year  = _strip(_first(raw.get("YearBuilt"), raw.get("EffectiveYearBuilt")))
    kind  = _property_kind_from_text(_first(raw.get("PropertyType"), raw.get("LandUse"), raw.get("UseCode")))

    # Geo
    lat   = _as_float(_first(raw.get("Latitude"), raw.get("GeoLatitude")))
    lng   = _as_float(_first(raw.get("Longitude"), raw.get("GeoLongitude")))

    # Values
    avm   = _as_int(_first(raw.get("AVMValue"), raw.get("EstimatedValue")))
    rent  = _as_int(_first(raw.get("RentValue"), raw.get("EstimatedRent")))

    return {
        "provider": "melissa",
        "fullAddress": _strip(addr1),
        "city": _strip(city), "state": _strip(state), "zipcode": _strip(zipc),
        "lat": lat, "lng": lng,
        "beds": beds, "baths": baths, "sqft": sqft, "lotSize": lot, "yearBuilt": year,
        "propertyType": kind,
        "melissaValue": avm, "rentEstimate": rent,
        "rawMelissa": raw,
    }

# ============================================================================
# SchoolDigger / WalkScore / RentCast (kept, slightly tidied)
# ============================================================================

def school_district_from_schooldigger(*, address:str=None, city:str=None, state:str=None,
                                      lat:Optional[float]=None, lng:Optional[float]=None) -> Tuple[Optional[str], Optional[str]]:
    app_id  = _cfg("SCHOOLDIGGER_APP_ID")
    app_key = _cfg("SCHOOLDIGGER_APP_KEY")
    if not (app_id and app_key):
        return None, None

    try:
        if lat is not None and lng is not None:
            url = "https://api.schooldigger.com/v2.0/districts"
            params = {"lat": lat, "lng": lng, "distance": 5, "appID": app_id, "appKey": app_key}
            r = requests.get(url, params=params, timeout=(5, 15))
            if r.ok:
                data = r.json()
                items = data.get("districts") or data.get("data") or []
                if items:
                    name = _first(items[0].get("districtName"), items[0].get("name"))
                    return (name, "schooldigger")

        if city and state:
            url = "https://api.schooldigger.com/v2.0/districts"
            params = {"st": state, "q": city, "appID": app_id, "appKey": app_key}
            r = requests.get(url, params=params, timeout=(5, 15))
            if r.ok:
                data = r.json()
                items = data.get("districts") or data.get("data") or []
                if items:
                    name = _first(items[0].get("districtName"), items[0].get("name"))
                    return (name, "schooldigger")
    except requests.RequestException:
        pass
    return None, None

def walk_transit_from_walkscore(*, address:str, lat:float, lng:float) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    key = _cfg("WALK_SCORE_API_KEY")
    if not key or lat is None or lng is None or not address:
        return None, None
    try:
        ws = None
        url_ws = "https://api.walkscore.com/score"
        params_ws = {"format": "json", "address": address, "lat": lat, "lon": lng, "wsapikey": key}
        r1 = requests.get(url_ws, params=params_ws, timeout=(5, 15))
        if r1.ok:
            j = r1.json()
            if isinstance(j, dict) and _ok(j.get("walkscore")):
                ws = {"walkScore": j.get("walkscore")}
        ts = None
        url_ts = "https://transit.walkscore.com/transit/score/"
        params_ts = {"lat": lat, "lon": lng, "wsapikey": key}
        r2 = requests.get(url_ts, params=params_ts, timeout=(5, 15))
        if r2.ok:
            jt = r2.json()
            if isinstance(jt, dict) and _ok(jt.get("transit_score")):
                ts = jt.get("transit_score")
        result = {}
        if ws: result.update(ws)
        if ts is not None: result["transitScore"] = ts
        return (result or None, "walkscore")
    except requests.RequestException:
        return None, None

def rent_from_rentcast(*, address:str=None, city:str=None, state:str=None, zipcode:str=None) -> Tuple[Optional[int], Optional[str]]:
    key = _cfg("RENTCAST_API_KEY")
    if not key:
        return None, None
    addr = address or ""
    if city and state and zipcode:
        addr = f"{addr}, {city}, {state} {zipcode}".strip(", ")
    if not addr:
        return None, None
    try:
        url = "https://api.rentcast.io/v1/avm/rents"
        headers = {"X-Api-Key": key}
        params = {"address": addr}
        r = requests.get(url, headers=headers, params=params, timeout=(5, 15))
        if r.ok:
            j = r.json()
            est = _first(j.get("rent"), j.get("rentEstimate"), j.get("estimate"))
            if _ok(est):
                return _as_int(est), "rentcast"
    except requests.RequestException:
        pass
    return None, None

# ============================================================================
# Merge + ARV choice + Initial Offer math
# ============================================================================

def merge_details(z: Optional[Dict[str, Any]], m: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Merge Zillow/Melissa into one details dict. Zillow wins on beds/baths/sqft
    if present; Melissa fills gaps. Also preserve both valuation candidates.
    """
    d: Dict[str, Any] = {}
    prov: Dict[str, str] = {}

    if z:
        d.update({k: v for k, v in z.items() if k not in ("raw",)})
        d["rawZillow"] = z.get("raw")
        prov["core"] = "zillow"

    # Fill gaps from Melissa
    if m:
        def fill(k_src, k_dst=None):
            dst = k_dst or k_src
            if not _ok(d.get(dst)) and _ok(m.get(k_src)):
                d[dst] = m[k_src]
                prov.setdefault("filled", "melissa")
        for k in ("fullAddress","city","state","zipcode","lat","lng","beds","baths","sqft","lotSize","yearBuilt","propertyType","rentEstimate"):
            fill(k)
        if _ok(m.get("melissaValue")):
            d["melissaValue"] = m["melissaValue"]
        # If Zillow didn’t provide a zestimate but Melissa has AVM, okay.
        prov.setdefault("core", "melissa") if not z else None
        d["rawMelissa"] = m.get("rawMelissa")

    # If neither set propertyType, leave None
    d["provenance"] = prov
    return d

def choose_arv(details: Dict[str, Any]) -> Optional[int]:
    """
    Simple, deterministic ARV chooser:
    - If both Zillow zestimate & Melissa AVM present and close (<=10% apart): average.
    - If both present but far: take the median of {zest, melissa, sqft*$/sqft guess? (omit for determinism here)}.
    - Else whichever is present.
    """
    z = _as_int(details.get("zestimate"))
    m = _as_int(details.get("melissaValue"))
    if z and m:
        hi = max(z, m)
        lo = min(z, m)
        if hi and lo and lo > 0:
            spread = (hi - lo) / lo
            if spread <= 0.10:
                return _as_int((z + m) / 2.0)
        # If far apart, take the middle value between z and m (same as min/max here)
        return _as_int((z + m) / 2.0)
    return z or m

def initial_offer_from_arv(arv: Optional[int], condition_1_10: Optional[Union[int, str]], property_kind: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Condition rule:
      - Start at ARV. For each point below 10, subtract 4.5% of ARV (this bucket is 'repairs').
    Returns:
      { 'arv': int, 'repairs': int, 'my_cash_offer': int, 'notes': str }
    """
    if not arv:
        return None
    try:
        cond = int(condition_1_10) if condition_1_10 not in (None, "",) else 7
    except Exception:
        cond = 7
    if cond < 1: cond = 1
    if cond > 10: cond = 10

    points_below = 10 - cond
    reduction_pct = 0.045 * points_below  # 4.5% per point
    repairs = int(round(arv * reduction_pct))
    # Cash offer baseline: ARV - repairs - (basic spread). Start simple: just ARV - repairs.
    cash_offer = max(0, arv - repairs)

    # Basic owner-finance sketch (optional): smaller discount, higher price OK
    # This is just a seed for your Offer form; you can refine in UI.
    owner_fin_price = int(round(arv * (1.00 - (reduction_pct * 0.5))))  # softer discount than cash
    owner_fin_notes = "Owner-finance baseline price with softer discount; terms to be set (rate/DP/months)."

    notes = f"Auto offer using condition={cond} ⇒ repairs={repairs:,} (4.5%/pt below 10)."
    return {
        "arv": arv,
        "repairs": repairs,
        "my_cash_offer": cash_offer,
        "owner_fin_price": owner_fin_price,
        "kind": property_kind or "Unknown",
        "notes": notes,
    }

def build_initial_offers(details: Dict[str, Any], condition_1_10: Optional[Union[int,str]]) -> List[Dict[str, Any]]:
    arv = choose_arv(details)
    kind = details.get("propertyType")
    base = initial_offer_from_arv(arv, condition_1_10, kind)
    if not base:
        return []
    offers: List[Dict[str, Any]] = []

    # Cash
    offers.append({
        "deal_type": "Cash",
        "deal_kind": "Flip" if kind in ("SFR","Multifamily") else "Land",
        "arv": base["arv"],
        "repairs_flip": base["repairs"],
        "my_cash_offer": base["my_cash_offer"],
        "notes": base["notes"],
        "initial_offer": True,
    })

    # Owner finance (placeholder structure; UI will set rate/term/DP)
    offers.append({
        "deal_type": "Owner Finance",
        "deal_kind": "Rental" if kind in ("SFR","Multifamily") else "Land",
        "arv": base["arv"],
        "repairs_rental": base["repairs"],
        "end_buyer_price": base["owner_fin_price"],
        "notes": base.get("owner_fin_notes", "Owner-finance baseline; finalize terms after intake."),
        "initial_offer": True,
    })
    return offers

# ============================================================================
# Orchestrators you can call from routes
# ============================================================================

def fetch_details_from_sources(address: str, city: Optional[str]=None, state: Optional[str]=None, zipcode: Optional[str]=None) -> Dict[str, Any]:
    """
    1) Try Zillow (zpid → property).
    2) Fallback to Melissa.
    3) Merge.
    """
    z: Optional[Dict[str, Any]] = None
    m: Optional[Dict[str, Any]] = None

    # Zillow
    zpid = zillow_find_zpid_by_address(address)
    if zpid:
        zraw = zillow_property_by_zpid(zpid)
        if zraw:
            z = parse_zillow_details(zraw)

    # Melissa fallback or supplement
    # If Zillow already delivered a clean address, override inputs for Melissa precision
    a_addr = _first((z or {}).get("fullAddress"), address)
    a_city = _first((z or {}).get("city"), city)
    a_state = _first((z or {}).get("state"), state)
    a_zip = _first((z or {}).get("zipcode"), zipcode)

    mraw = melissa_property_by_address(a_addr or address, a_city, a_state, a_zip)
    if mraw:
        m = parse_melissa_details(mraw)

    return merge_details(z, m)

def enrich_details_misc(details: Dict[str, Any]) -> Dict[str, Any]:
    """
    Keep your existing district / walkscore / rentcast enrichers.
    """
    d = dict(details or {})
    provenance: Dict[str, str] = dict(d.get("provenance") or {})

    fulladdr = d.get("fullAddress")
    lat, lng = d.get("lat"), d.get("lng")
    city, state, zipcode = d.get("city"), d.get("state"), d.get("zipcode")

    # School district
    if not _ok(d.get("schoolDistrict")):
        sd, src = school_district_from_schooldigger(address=fulladdr, city=city, state=state, lat=lat, lng=lng)
        if _ok(sd):
            d["schoolDistrict"] = sd
            provenance["schoolDistrict"] = src or "schooldigger"

    # Walk/transit
    wt = d.get("walkTransit")
    if not (isinstance(wt, dict) and (_ok(wt.get("walkScore")) or _ok(wt.get("transitScore")))):
        if _ok(fulladdr) and (lat is not None and lng is not None):
            scored, src = walk_transit_from_walkscore(address=fulladdr, lat=lat, lng=lng)
            if _ok(scored):
                d["walkTransit"] = scored
                provenance["walkTransit"] = src or "walkscore"

    # Rent
    if not _ok(d.get("rentEstimate")):
        rent, src = rent_from_rentcast(address=fulladdr, city=city, state=state, zipcode=zipcode)
        if _ok(rent):
            d["rentEstimate"] = rent
            provenance["rentEstimate"] = src or "rentcast"

    d["provenance"] = provenance
    return d

def enrich_and_price(*, address: str, city: Optional[str], state: Optional[str], zipcode: Optional[str],
                     condition_1_10: Optional[Union[int,str]]=7) -> Dict[str, Any]:
    """
    High-level: fetch → merge → misc enrich → choose ARV → compute initial offers.
    Use this right after saving a new Lead (pass lead.condition).
    """
    details = fetch_details_from_sources(address=address, city=city, state=state, zipcode=zipcode)
    details = enrich_details_misc(details)

    # ARV pick is implicit in initial offer builder
    details["initial_offers"] = build_initial_offers(details, condition_1_10)
    details["arv_estimate"] = choose_arv(details)  # store top-level for convenience
    return details

def bootstrap_property_from_address(address: str, city: Optional[str]=None, state: Optional[str]=None, zipcode: Optional[str]=None,
                                    condition_1_10: Optional[Union[int,str]]=7) -> Dict[str, Any]:
    """
    Alias with a friendlier name for routes that auto-create a Property + initial Offers.
    """
    return enrich_and_price(address=address, city=city, state=state, zipcode=zipcode, condition_1_10=condition_1_10)
