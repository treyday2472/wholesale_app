# app/services/attom.py
import os, logging, requests
from datetime import datetime, timedelta

log = logging.getLogger(__name__)
ATTOM_BASE = "https://api.gateway.attomdata.com"

class AttomError(Exception):
    pass

def _normalize_kind(*vals):
    """
    Normalize property type/kind to buckets: 'sfr', 'townhouse', 'condo', 'multi', 'residential', or None.
    Accepts any number of strings and inspects them together.
    """
    s = " ".join([str(v) for v in vals if v]).lower()
    if not s:
        return None
    if "condo" in s or "condominium" in s:
        return "condo"
    if "town" in s and "house" in s:
        return "townhouse"
    if any(k in s for k in ["duplex", "triplex", "fourplex", "quad", "quadplex", "multi", "apartment", "multi-family", "multifamily"]):
        return "multi"
    if "sfr" in s or "single family" in s or "single-family" in s:
        return "sfr"
    if "residential" in s:
        return "residential"
    return None


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

def extract_comps(payload: dict, max_items: int = 50):
    """
    Flatten ATTOM sale snapshot payload into a compact list for the UI/filters/AI.
    - Fills price from multiple sale nodes (amount.saleamt, saleTrans, saleRec).
    - Attaches a normalized 'kind' field for apples-to-apples filtering.
    """
    out = []
    props = (payload or {}).get("property") or []
    for p in props[: max_items or 50]:
        addr = (p.get("address") or {})
        bld  = (p.get("building") or {})
        rms  = (bld.get("rooms") or {})
        siz  = (bld.get("size") or {})
        loc  = (p.get("location") or {})
        summ = (p.get("summary") or {})
        sale = (p.get("sale") or {})

        # --- price (robust) ---
        amount = sale.get("amount") or {}
        price  = (
            amount.get("saleamt")
            or sale.get("saleamt")
            or (sale.get("calculation") or {}).get("amount")    # very rare
        )
        try:
            price = float(price) if price is not None else None
        except Exception:
            price = None

        # --- sale date (robust) ---
        sale_date = (
            sale.get("saleTransDate")
            or sale.get("salesearchdate")
            or amount.get("salerecdate")
            or sale.get("saleRecDate")
        )

        # --- kind ---
        kind = _normalize_kind(
            summ.get("propLandUse"),
            summ.get("propclass"),
            summ.get("propertyType"),
            summ.get("propsubtype"),
            summ.get("proptype"),
        )

        item = {
            "address1": addr.get("line1"),
            "city": addr.get("locality"),
            "state": addr.get("countrySubd"),
            "postalcode": addr.get("postal1") or addr.get("postalCode"),
            "address": addr.get("oneLine")
                or (", ".join([addr.get("line1", ""), addr.get("line2", "")]).strip(", ")),
            "beds": rms.get("beds"),
            "baths": rms.get("bathstotal") or rms.get("bathsfull"),
            "sqft": siz.get("universalsize") or siz.get("livingsize") or siz.get("bldgsize"),
            "yearBuilt": (summ.get("yearbuilt") or bld.get("yearbuilt")),
            "distance": loc.get("distance"),
            "lat": loc.get("latitude"),
            "lng": loc.get("longitude"),
            "saleDate": sale_date,
            "price": price,
            "kind": kind,
        }
        out.append(item)
    return out

from datetime import datetime, timedelta

def filter_comps_rules(
    comps: list,
    *,
    subject_sqft: float | int | None = None,
    subject_year: int | None = None,
    subject_subdivision: str | None = None,
    max_months: int = 6,
    max_radius_miles: float = 0.5,
    sqft_tolerance: float = 0.15,
    year_tolerance: int = 5,
    require_subdivision: bool = False,
    subject_prop_kind: str | None = None,
    strict_type_match: bool = True,
):
    """
    Apply simple, transparent filtering to comps.
    Now also enforces 'same property kind' (SFR vs Condo/Townhouse/Multi) when available.
    """
    def _parse_date(d):
        if not d:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(str(d)[:10], fmt)
            except Exception:
                pass
        return None

    subj_kind = _normalize_kind(subject_prop_kind)
    subj_sub  = (subject_subdivision or "").strip().lower() if subject_subdivision else None
    now = datetime.utcnow()

    kept = []
    for c in comps or []:
        # type filter
        if strict_type_match and subj_kind:
            ck = _normalize_kind(c.get("kind"))
            if ck != subj_kind:
                # allow SFR ~ Townhouse as a soft match if you want to be lenient; otherwise drop
                soft_equiv = (subj_kind == "sfr" and ck == "townhouse")
                if not soft_equiv:
                    continue

        # months filter
        dt = _parse_date(c.get("saleDate"))
        if dt:
            delta_months = (now.year - dt.year) * 12 + (now.month - dt.month)
            if delta_months > max_months:
                continue

        # radius filter
        dist = c.get("distance")
        try:
            if dist is not None and float(dist) > float(max_radius_miles):
                continue
        except Exception:
            pass

        # sqft tolerance
        if subject_sqft and c.get("sqft"):
            try:
                if abs(float(c["sqft"]) - float(subject_sqft)) / float(subject_sqft) > float(sqft_tolerance):
                    continue
            except Exception:
                pass

        # year tolerance
        if subject_year and c.get("yearBuilt"):
            try:
                if abs(int(c["yearBuilt"]) - int(subject_year)) > int(year_tolerance):
                    continue
            except Exception:
                pass

        # subdivision match (if required)
        if require_subdivision and subj_sub:
            line = (c.get("address") or "").lower()
            if subj_sub not in line:
                continue

        kept.append(c)

    # Prefer most recent + closest
    def _key(c):
        d = _parse_date(c.get("saleDate")) or datetime(1900, 1, 1)
        dist = c.get("distance") or 9999
        try:
            dist = float(dist)
        except Exception:
            dist = 9999
        return (-d.timestamp(), dist)

    kept.sort(key=_key)
    return kept



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
