# app/services/attom.py
import os, logging, requests
from datetime import datetime, timedelta

log = logging.getLogger(__name__)
ATTOM_BASE = "https://api.gateway.attomdata.com"

class AttomError(Exception):
    pass

def _key():
    k = os.getenv("ATTOM_API_KEY")
    if not k:
        try:
            from flask import current_app
            k = (current_app and current_app.config.get("ATTOM_API_KEY")) or None
        except Exception:
            k = None
    if not k:
        raise AttomError("Missing ATTOM_API_KEY")
    return k

def _get(path: str, params: dict):
    url = ATTOM_BASE + path
    headers = {"accept": "application/json", "apikey": _key()}
    log.info("[ATTOM] GET %s params=%s", path, params)
    r = requests.get(url, headers=headers, params=params, timeout=25)
    if r.status_code != 200:
        raise AttomError(f"HTTP {r.status_code}: {r.text[:500]}")
    try:
        return r.json()
    except Exception:
        raise AttomError("Non-JSON response from ATTOM")

# ---------------- Param helpers ----------------

def _addr_params(address1=None, city=None, state=None, postalcode=None, lat=None, lon=None):
    """
    If BOTH lat and lon are provided -> include them.
    Otherwise: include address1 and build address2 ("City, ST [ZIP]") or fall back to postalcode.
    """
    p = {}
    if lat is not None and lon is not None:
        p["lat"] = lat
        p["lon"] = lon

    if address1:
        p["address1"] = address1

    addr2 = None
    if city and state:
        addr2 = f"{city}, {state}"
        if postalcode:
            addr2 = f"{addr2} {postalcode}"
    elif state and postalcode:
        addr2 = f"{state} {postalcode}"
    if addr2:
        p["address2"] = addr2
    elif postalcode and "address1" in p and ("lat" not in p and "lon" not in p):
        # Some endpoints accept postalcode when address2 isn't available
        p["postalcode"] = postalcode

    return p

# ---------------- Core endpoints ----------------

def property_detail(address1=None, city=None, state=None, postalcode=None):
    """
    ATTOM Property Detail expects address1 + address2 ("City, ST [ZIP]") OR postalcode.
    Do NOT send lat/lon here.
    """
    if not address1:
        raise AttomError("property_detail requires address1")
    p = _addr_params(address1=address1, city=city, state=state, postalcode=postalcode, lat=None, lon=None)
    # strip lat/lon if any slipped in
    p.pop("lat", None); p.pop("lon", None)
    return _get("/propertyapi/v1.0.0/property/detail", p)

def avm(address1=None, city=None, state=None, postalcode=None, lat=None, lon=None):
    # safer to use ADDRESS for AVM to avoid LAT/LON parameter errors on some accounts
    p = _addr_params(address1=address1, city=city, state=state, postalcode=postalcode, lat=None, lon=None)
    return _get("/propertyapi/v1.0.0/attomavm/detail", p)

def rental_avm(address1=None, city=None, state=None, postalcode=None, lat=None, lon=None):
    p = _addr_params(address1=address1, city=city, state=state, postalcode=postalcode, lat=None, lon=None)
    return _get("/propertyapi/v1.0.0/valuation/rentalavm", p)

def sale_comps(address1=None, city=None, state=None, postalcode=None,
               radius_miles=1.0, min_beds=None, max_beds=None,
               min_baths=None, max_baths=None, page_size=25):
    """
    Use address/ZIP for snapshot; do NOT send lat/lon or minSaleDate.
    """
    params = {}
    if address1:   params["address1"] = address1
    if city and state:
        params["address2"] = f"{city}, {state}"
    elif postalcode:
        params["postalcode"] = postalcode

    params.update({"radius": radius_miles, "pageSize": page_size})
    if min_beds is not None:  params["minBeds"] = min_beds
    if max_beds is not None:  params["maxBeds"] = max_beds
    if min_baths is not None: params["minBaths"] = min_baths
    if max_baths is not None: params["maxBaths"] = max_baths

    return _get("/propertyapi/v1.0.0/sale/snapshot", params)

def detail_with_schools(address1=None, city=None, state=None, postalcode=None, lat=None, lon=None):
    p = _addr_params(address1=address1, city=city, state=state, postalcode=postalcode, lat=None, lon=None)
    return _get("/propertyapi/v1.0.0/property/detailwithschools", p)

# ---------------- Extractors ----------------

def extract_detail_coords(detail: dict):
    try:
        props = (detail or {}).get("property", [])
        if not props: return (None, None)
        loc = props[0].get("location", {}) or {}
        lat = loc.get("latitude"); lon = loc.get("longitude")
        if not lat or not lon: return (None, None)
        return (float(lat), float(lon))
    except Exception:
        return (None, None)

def extract_detail_basics(detail: dict):
    """
    Returns dict: fullAddress, postal, beds, baths, sqft, yearBuilt, lat, lng
    """
    out = {}
    try:
        props = (detail or {}).get("property", [])
        if not props: return out
        r = props[0]
        addr = r.get("address", {}) or {}
        bld  = r.get("building", {}) or {}
        rooms= bld.get("rooms", {}) or {}
        size = bld.get("size", {}) or {}
        summ = (r.get("summary") or bld.get("summary") or {}) or {}
        loc  = r.get("location", {}) or {}
        out = {
            "fullAddress": addr.get("oneLine") or (", ".join([addr.get("line1",""), addr.get("line2","")]).strip(", ")),
            "postal": addr.get("postal1"),
            "beds": rooms.get("beds"),
            "baths": rooms.get("bathstotal") or rooms.get("bathsfull"),
            "sqft": size.get("livingsize") or size.get("bldgsize") or size.get("universalsize"),
            "yearBuilt": summ.get("yearbuilt"),
            "lat": loc.get("latitude"),
            "lng": loc.get("longitude"),
        }
        return out
    except Exception:
        return out

def extract_avm_numbers(avm_payload: dict):
    try:
        props = (avm_payload or {}).get("property", [])
        if not props: return (None, None, None, None, None)
        a = (props[0].get("avm", {}) or props[0].get("attomAvm", {}))
        value = a.get("amount", {}).get("value")
        low   = a.get("amount", {}).get("low")
        high  = a.get("amount", {}).get("high")
        as_of = a.get("lastModified") or a.get("date")
        conf  = a.get("calcConfidence") or a.get("confidenceScore")
        return (value, low, high, as_of, conf)
    except Exception:
        return (None, None, None, None, None)

def extract_rental_avm_numbers(r_payload: dict):
    try:
        p = (r_payload or {}).get("property", [])
        if not p: return (None, None, None, None)
        r = p[0].get("rentalAvm", {}) or {}
        value = r.get("amount", {}).get("value")
        low   = r.get("amount", {}).get("low")
        high  = r.get("amount", {}).get("high")
        as_of = r.get("lastModified") or r.get("date")
        return (value, low, high, as_of)
    except Exception:
        return (None, None, None, None)

def extract_comps(snapshot: dict, max_items=10):
    out = []
    for row in (snapshot or {}).get("sale", [])[:max_items]:
        a = row.get("address", {}) or {}
        b = row.get("building", {}) or {}
        o = row.get("saleSearch", {}) or row.get("sale", {}) or {}
        out.append({
            "address": ", ".join([a.get("line1",""), a.get("locality",""), a.get("countrySubd",""), a.get("postal1","")]).strip(", "),
            "saleDate": o.get("saleTransDate") or o.get("saleDate"),
            "price": (o.get("saleAmt") or o.get("amount")),
            "beds": b.get("rooms", {}).get("beds"),
            "baths": b.get("rooms", {}).get("bathsFull"),
            "sqft": b.get("size", {}).get("grossSize"),
            "distance": row.get("location", {}).get("distance"),
        })
    return out

def extract_schools(detail_with_schools_payload: dict, max_items=5):
    out = []
    props = (detail_with_schools_payload or {}).get("property", [])
    if not props: return out
    sch = props[0].get("school", []) or []
    for s in sch[:max_items]:
        out.append({
            "name": s.get("schoolName"),
            "grades": (f"{s.get('lowGrade','')}-{s.get('highGrade','')}"
                       if (s.get('lowGrade') or s.get('highGrade')) else None),
            "type": s.get("schoolType"),
            "distance": s.get("distance"),
            "rating": s.get("rating"),
            "district": s.get("districtName"),
        })
    return out
