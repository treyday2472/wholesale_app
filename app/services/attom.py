# app/services/attom.py
import os, logging, requests
from datetime import datetime, timedelta

log = logging.getLogger(__name__)
ATTOM_BASE = "https://api.gateway.attomdata.com"

class AttomError(Exception):
    pass

def _to_float(x):
    """Safe float parse that tolerates None, '', and '1,234'."""
    try:
        if x is None:
            return None
        if isinstance(x, (int, float)):
            return float(x)
        s = str(x).strip()
        if not s:
            return None
        return float(s.replace(",", ""))
    except Exception:
        return None

def _parse_date_any(s):
    """
    Parse a bunch of common ATTOM date shapes and return a datetime.date or None.
    Handles: 'YYYY-MM-DD', 'YYYY/MM/DD', 'MM/DD/YYYY', 'YYYY-MM',
             and the ISO-ish forms with time suffix.
    """
    if not s:
        return None
    s = str(s).strip()
    fmts = (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%Y-%m",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%fZ",
    )
    for fmt in fmts:
        try:
            dt = datetime.strptime(s[:len(fmt)], fmt)
            return dt.date()
        except Exception:
            continue
    return None

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
    # FIX: use proper header names
    headers = {"Accept": "application/json", "APIKey": _key()}
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
               min_baths=None, max_baths=None, page_size=25,
               lat=None, lon=None, last_n_months=None, order_by="saleSearchDate desc"):
    """
    Get comparable sales snapshot.
    Preferred: pass latitude & longitude. Fallback: address1 + address2 or postalcode.
    """
    params = {"radius": float(radius_miles), "pageSize": int(page_size), "orderBy": order_by}

    if lat is not None and lon is not None:
        params["latitude"]  = float(lat)
        params["longitude"] = float(lon)
    else:
        if address1:
            params["address1"] = address1
        if city and state:
            params["address2"] = f"{city}, {state}" + (f" {postalcode}" if postalcode else "")
        elif postalcode:
            params["postalcode"] = postalcode

    if min_beds is not None:  params["minBeds"] = int(min_beds)
    if max_beds is not None:  params["maxBeds"] = int(max_beds)
    if min_baths is not None: params["minBaths"] = float(min_baths)
    if max_baths is not None: params["maxBaths"] = float(max_baths)

    # Optional date window if your plan supports it
    # if last_n_months:
    #     from datetime import datetime, timedelta
    #     end = datetime.utcnow().date()
    #     start = end - timedelta(days=30*int(last_n_months))
    #     params["startCalendarDate"] = start.strftime("%Y/%m/%d")
    #     params["endCalendarDate"]   = end.strftime("%Y/%m/%d")

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

# --- add near your other helpers ---
from datetime import datetime, timedelta

_ALLOWED_DEED_TOKENS = ("DEED", "GRANT DEED", "WARRANTY DEED", "SPECIAL WARRANTY DEED", "QUIT CLAIM DEED")
_EXCLUDED_DOC_TOKENS = ("MORTGAGE", "DEED OF TRUST", "ASSIGNMENT", "RELEASE", "LIEN", "UCC", "FORECLOSURE")

def _first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None

def _is_deed_doc(doc):
    if not doc: return False
    d = str(doc).upper()
    if any(x in d for x in _EXCLUDED_DOC_TOKENS): return False
    return any(x in d for x in _ALLOWED_DEED_TOKENS) or d == "DEED"

def _parse_date(s):
    if not s: return None
    for fmt in ("%Y-%m-%d","%Y/%m/%d","%m/%d/%Y","%Y-%m-%dT%H:%M:%S","%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(s[:len(fmt)], fmt).date()
        except Exception:
            pass
    return None

def extract_comps(snapshot: dict, max_items=50, include_nulls=False):
    out = []
    props = (snapshot or {}).get("property", []) or []
    for row in props[:max_items]:
        a = row.get("address", {}) or {}
        b = row.get("building", {}) or {}
        rooms = b.get("rooms") or {}
        size  = b.get("size") or {}
        # IMPORTANT: summary lives at the ROW level on this endpoint
        summ  = row.get("summary") or b.get("summary") or {}
        loc   = row.get("location", {}) or {}
        sale  = row.get("sale", {}) or row.get("saleSearch", {}) or {}

        one_line = (
            a.get("oneLine")
            or ", ".join([a.get("line1",""), a.get("locality",""), a.get("countrySubd",""), a.get("postal1","")]).strip(", ")
            or None
        )

        amount = sale.get("amount") or {}
        doc    = (
            amount.get("saledoctype")
            or sale.get("saledoctype")
            or sale.get("saleDocType")
        )

        # price only trusted for deed-like docs; keep row anyway if not deed
        price_raw = _first(amount.get("saleamt"), sale.get("saleamt"), sale.get("saleAmt"))
        price     = _to_float(price_raw) if _is_deed_doc(doc) else None

        # capture many variants of the date key (ATTOM isn’t fully consistent)
        sale_date = _first(
            sale.get("saleTransDate"),
            sale.get("saleDate"),
            sale.get("salerecdate"),
            sale.get("saleSearchDate"),
            sale.get("salesearchdate"),
        )

        out.append({
            "address": one_line,
            "saleDate": sale_date,  # may be None; we won't drop solely for that
            "price": price,
            "beds": _first(rooms.get("beds"), b.get("bedrooms")),
            # INCLUDE bathstotal so you see baths in Aventura results
            "baths": _first(rooms.get("bathstotal"), rooms.get("bathsFull"), rooms.get("baths"), b.get("bathrooms")),
            "sqft": _first(size.get("livingsize"), size.get("bldgsize"), size.get("universalsize"), size.get("grossSize")),
            "distance": loc.get("distance"),
            "docType": doc,
            "transType": sale.get("saletranstype"),
            "yearBuilt": _first(summ.get("yearbuilt"), b.get("yearbuilt"), b.get("yearBuilt")),
            "subdivision": _first(
                loc.get("subdname"),
                summ.get("subdivision"),
                summ.get("Subdivision"),
                (row.get("lot") or {}).get("subdivision"),
                a.get("neighborhoodName"),
            ),
        })
    return out

def filter_comps_rules(
    comps,
    *,
    subject_sqft=None,
    subject_year=None,
    subject_subdivision=None,
    max_months=6,
    max_radius_miles=0.5,
    sqft_tolerance=0.15,
    year_tolerance=5,
    require_subdivision=False,
    min_price=0,
    max_price=None,
    collect_debug=False,
    drop_if_missing_date=False,  # <-- new toggle; default keep rows with no date
):
    # cutoff as datetime (not date) so we can compare safely
    from datetime import datetime, timedelta
    cutoff = None
    if max_months:
        try:
            from dateutil.relativedelta import relativedelta
            cutoff = datetime.utcnow() - relativedelta(months=int(max_months))
        except Exception:
            cutoff = datetime.utcnow() - timedelta(days=int(max_months * 30.5))

    def _norm(s): return (s or "").strip().lower()
    subj_sub = _norm(subject_subdivision)

    kept = []
    why = {"date": 0, "date_missing": 0, "radius": 0, "sqft": 0, "year": 0, "subd": 0, "price": 0}

    # precompute sqft band
    lo_sqft = subject_sqft * (1 - float(sqft_tolerance)) if subject_sqft else None
    hi_sqft = subject_sqft * (1 + float(sqft_tolerance)) if subject_sqft else None

    for c in comps:
        # DATE: only drop if a date exists and is older than cutoff
        d = _parse_date_any(c.get("saleDate"))
        if cutoff:
            if d is None:
                if collect_debug: why["date_missing"] += 1
                if drop_if_missing_date:
                    # strictly enforce date presence if caller requests it
                    continue
            else:
                # keep if recent; drop if older
                if d < cutoff.date():
                    if collect_debug: why["date"] += 1
                    continue

        # RADIUS
        try:
            dist = float(c["distance"]) if c.get("distance") not in (None, "", "null") else None
        except Exception:
            dist = None
        if dist is not None and max_radius_miles is not None and dist > float(max_radius_miles):
            if collect_debug: why["radius"] += 1
            continue

        # SQFT
        if subject_sqft and c.get("sqft"):
            try:
                sq = float(c["sqft"])
                if (lo_sqft and sq < lo_sqft) or (hi_sqft and sq > hi_sqft):
                    if collect_debug: why["sqft"] += 1
                    continue
            except Exception:
                # if sqft can't be parsed, don't auto-drop
                pass

        # YEAR
        if subject_year and c.get("yearBuilt"):
            try:
                cy = int(c["yearBuilt"])
                if abs(cy - int(subject_year)) > int(year_tolerance):
                    if collect_debug: why["year"] += 1
                    continue
            except Exception:
                pass

        # SUBDIVISION
        if require_subdivision and subj_sub:
            if _norm(c.get("subdivision")) != subj_sub:
                if collect_debug: why["subd"] += 1
                continue

        # PRICE sanity (optional)
        price = _to_float(c.get("price"))
        if price is not None:
            if price < float(min_price):
                if collect_debug: why["price"] += 1
                continue
            if max_price is not None and price > float(max_price):
                if collect_debug: why["price"] += 1
                continue

        kept.append(c)

    # newest (if any date) first, then nearest
    def _sort_key(x):
        d = _parse_date_any(x.get("saleDate"))
        ds = d.toordinal() if d else -1  # undated at bottom
        try:
            dist = float(x.get("distance")) if x.get("distance") not in (None, "", "null") else 9e9
        except Exception:
            dist = 9e9
        return (ds, -dist)

    kept.sort(key=_sort_key, reverse=True)
    return (kept, why) if collect_debug else kept



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
