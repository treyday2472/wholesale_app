import os, re, json, logging, requests
from datetime import datetime
from flask import current_app

log = logging.getLogger(__name__)

class MelissaHttpError(Exception): ...

def _cfg(key, default=""):
    if current_app and (key in current_app.config):
        return current_app.config.get(key, default)
    return os.environ.get(key, default)

def _api_key():
    key = _cfg("MELISSA_API_KEY") or _cfg("MELISSA_KEY")
    if not key:
        raise MelissaHttpError("Missing MELISSA_API_KEY")
    return key

def _get(url, params):
    red = {**params, "id": "***"}
    log.info("[Melissa] GET %s params=%s", url, red)
    r = requests.get(url, params=params, timeout=20)
    if r.status_code != 200:
        raise MelissaHttpError(f"HTTP {r.status_code}: {r.text[:400]}")
    try:
        return r.json()
    except Exception:
        raise MelissaHttpError("Non-JSON response from Melissa")

# ---------- Lookups ----------

def lookup_property(
    *, addr=None, a1=None, city=None, state=None, postal=None,
    country="US",
    cols="GrpAll",
    ff=None,  # Free-form address string, e.g. "710 Winston Ln Sugar Land TX"
):
    """
    LookupProperty supports:
      - a1 + (city/state[/postal])  OR
      - ff (free-form single line)
    Only include 'ff' in the request if you pass an actual address string.
    """
    key  = _api_key()
    base = _cfg("MELISSA_PROP_URL", "https://property.melissadata.net/v4/WEB/LookupProperty/")
    if addr and not a1:
        a1 = addr

    params = {
        "id": key,
        "format": "JSON",
        "a1": a1 or "",
        "city": city or "",
        "state": state or "",
        "postal": postal or "",
        "country": country or "US",
        "cols": cols or "",
    }
    if isinstance(ff, str) and ff.strip():
        params["ff"] = ff.strip()

    return _get(base, params)

def lookup_deeds(*, ff=None, fips=None, apn=None, mak=None, txid=None,
                 cols=None, opt=None):
    """
    LookupDeeds takes ff OR (fips+apn) OR mak OR txid (deprecated).
    """
    key  = _api_key()
    base = _cfg("MELISSA_DEEDS_URL", "https://property.melissadata.net/v4/WEB/LookupDeeds/")
    if not any([ff, (fips and apn), mak, txid]):
        raise MelissaHttpError("LookupDeeds requires ff OR (fips+apn) OR mak/txid")

    # Default to the deed/mortgage groups we need
    cols = cols or ",".join([
        "GrpDocInfo",        # recording/instrument/date/type
        "GrpTxDefInfo",      # transaction type
        "GrpTxAmtInfo",      # transfer amount
        "GrpPrimaryGrantor",
        "GrpPrimaryGrantee",
        "GrpMortgage1",      # <-- mortgage info
        # "GrpMortgage2",    # deprecated
    ])

    params = {
        "id": key,
        "format": "JSON",
        "cols": cols,
    }
    if opt:
        params["opt"] = opt

    if fips and apn:
        params["fips"] = fips
        params["apn"]  = apn
    elif ff:
        params["ff"] = ff
    elif mak:
        params["mak"] = mak
    elif txid:
        params["txid"] = txid

    return _get(base, params)

# ---------- Normalization ----------

_NUM = re.compile(r"[^\d.]")

def _to_int(x):
    if x is None: return None
    if isinstance(x, (int, float)): return int(float(x))
    s = str(x).strip()
    if s == "" or s == "0" or s == "0.00": return None
    try:
        return int(float(_NUM.sub("", s)))
    except Exception:
        return None

def _fmt_ymd(s):
    if not s: return None
    s = str(s)
    # API may return 20240604 or 2024-06-04
    if len(s) == 8 and s.isdigit():
        try: return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")
        except Exception: return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    except Exception:
        return None

def _normalize_bool(s):
    if s is None: return None
    if isinstance(s, bool): return s
    t = str(s).strip().lower()
    if t in ("y","yes","true","1"): return True
    if t in ("n","no","false","0"): return False
    return None

def _same_address(prop_addr: dict, owner_addr: dict):
    if not prop_addr or not owner_addr: return None
    keys = ("AddressLine1","City","State","Postal")
    p = " ".join(str(prop_addr.get(k,"") or "").lower() for k in keys).strip()
    o = " ".join(str(owner_addr.get(k,"") or "").lower() for k in keys).strip()
    if not p or not o: return None
    return p == o

MORTGAGE_TYPE_MAP = {
    "0":"Unknown","3":"Building/Construction","6":"Line of Credit","8":"FHA",
    "10":"Conventional","16":"VA","101":"USDA","102":"Farmers Home",
    "103":"Commercial","104":"State Veterans","105":"Reverse",
    "121":"Assumption","123":"ARM (Adjustable)","124":"Closed End",
    "125":"Seller Carryback","126":"Stand Alone First","127":"Stand Alone Refi",
    "128":"Stand Alone Second","129":"Refi-Assignment","130":"Second for Down Payment",
    "131":"Land Contract","132":"Loan Mod","133":"SBA","134":"Cash","135":"Neg-Am"
}

DEED_TYPE_MAP = {
    "DTWD":"Warranty Deed","DTQC":"Quit Claim Deed","DTGD":"Grant Deed","DTEX":"Executor's Deed",
    "DTQC":"Quit Claim Deed","DTWD":"Warranty Deed","DTQC":"Quit Claim Deed",
    "DTQC":"Quit Claim Deed","DTQC":"Quit Claim Deed"
}
# (The deed type list is very long; we map common ones and fall back to the raw code.)

def _best_sale_from_deeds(deeds):
    """
    Choose the latest deed with a non-zero TransferAmount.
    Returns (price:int|None, date:'YYYY-MM-DD'|None, purchase_method:str|None)
    """
    recs = (deeds or {}).get("Records") or []
    best = (None, None, None)  # (price, date, type_label)
    best_dt = datetime.min

    for r in recs:
        tx_amt = (r.get("TxAmtInfo") or {})
        doc    = (r.get("DocInfo") or {})
        price  = _to_int(tx_amt.get("TransferAmount"))
        date   = _fmt_ymd(doc.get("RecordingDate") or doc.get("InstrumentDate"))
        if not price or not date:
            continue
        try_dt = datetime.strptime(date, "%Y-%m-%d")
        if try_dt >= best_dt:
            type_code = (doc.get("TypeCode") or "").strip()
            label = DEED_TYPE_MAP.get(type_code, type_code or None)
            best   = (price, date, label)
            best_dt= try_dt
    return best

def _best_mortgage_from_deeds(deeds):
    """
    Pick the most relevant mortgage from Mortgage1 across all deed records.
    Preference: larger Amount, then latest TermDate/RecordingDate.
    """
    recs = (deeds or {}).get("Records") or []
    best = {}
    best_score = (-1, datetime.min)  # (amount or 0, date)

    for r in recs:
        m1  = (r.get("Mortgage1") or {})
        amt = _to_int(m1.get("Amount"))
        typ = m1.get("Type")
        lender = m1.get("LenderFullName") or m1.get("LenderFirstName") or None
        rec_date = _fmt_ymd(m1.get("TermDate")) or _fmt_ymd(m1.get("RecordingDate"))
        dt = datetime.min
        if rec_date:
            try: dt = datetime.strptime(rec_date, "%Y-%m-%d")
            except Exception: dt = datetime.min

        score = (amt or 0, dt)
        if score > best_score and any([amt, typ, lender, rec_date]):
            best = {
                "mortgageAmount": amt,
                "mortgageType": MORTGAGE_TYPE_MAP.get(str(typ), str(typ) if typ not in (None,"") else None),
                "lender": lender,
                "mortgageOriginationDate": rec_date,
            }
            best_score = score

    return best

def normalize_property_record(prop_record: dict, deeds_payload: dict | None = None):
    """
    Create a compact block the UI consumes while you still store raw payloads.
    """
    r = prop_record or {}

    # ---- sales from LookupProperty (fallback) ----
    sale = r.get("SaleInfo", {}) or {}
    last_date = _fmt_ymd(sale.get("DeedLastSaleDate")) or _fmt_ymd(sale.get("AssessorLastSaleDate"))
    last_price = _to_int(sale.get("DeedLastSalePrice")) or _to_int(sale.get("AssessorLastSaleAmount"))
    purchase_method = (r.get("CurrentDeed") or {}).get("DeedType") or None

    # Prefer deeds-derived sale if available
    if deeds_payload:
        d_price, d_date, d_type = _best_sale_from_deeds(deeds_payload)
        if d_price: last_price = d_price
        if d_date:  last_date  = d_date
        if d_type:  purchase_method = purchase_method or d_type

    # owner occupied heuristic (not always present)
    owner_occ = _normalize_bool(r.get("OwnerOccupied"))
    if owner_occ is None:
        owner_occ = _same_address(r.get("PropertyAddress"), r.get("OwnerAddress"))

    # ---- mortgage from LookupProperty.CurrentDeed, then Deeds ----
    cd = r.get("CurrentDeed", {}) or {}
    mort_amount = _to_int(cd.get("MortgageAmount"))
    mort_type   = cd.get("MortgageType")
    mort_lender = cd.get("LenderName")
    mort_date   = _fmt_ymd(cd.get("MortgageDate") or cd.get("RecordingDate") or cd.get("MortgageOriginationDate"))

    if deeds_payload:
        picked = _best_mortgage_from_deeds(deeds_payload)
        mort_amount = mort_amount or picked.get("mortgageAmount")
        mort_type   = mort_type   or picked.get("mortgageType")
        mort_lender = mort_lender or picked.get("lender")
        mort_date   = mort_date   or picked.get("mortgageOriginationDate")

    ownership = {
        "lastSoldPrice": last_price,
        "lastSoldDate": last_date,
        "purchaseMethod": purchase_method,
        "ownerOccupied": owner_occ,
        "mortgageAmount": mort_amount,
        "mortgageType": mort_type,
        "lender": mort_lender,
        "mortgageOriginationDate": mort_date,
    }

    # ---- basic structure ----
    size  = r.get("PropertySize", {}) or {}
    rooms = r.get("IntRoomInfo", {}) or {}
    use   = r.get("PropertyUseInfo", {}) or {}
    structure = {
        "beds": _to_int(rooms.get("BedroomsCount")),
        "baths": _to_int(rooms.get("BathCount")),
        "sqft": _to_int(size.get("AreaBuilding")),
        "year_built": _to_int(use.get("YearBuilt")),
    }

    classification = {
        "propertyTypeRaw": use.get("PropertyType") or None,
        "classification": use.get("PropertyTypeDescription") or None,
        "unitCount": _to_int(use.get("UnitsCount")),
    }

    meta = {
        "sources": {
            "ownership_mortgage": "Melissa (LookupProperty + LookupDeeds)",
            "bedrooms": "Melissa Public Record",
            "bathrooms": "Melissa Public Record",
            "livingArea": "Melissa Public Record",
            "yearBuilt": "Melissa Public Record",
            "lotSize": "Melissa Public Record",
        },
        "as_of": {
            "ownership_mortgage": datetime.utcnow().strftime("%Y-%m-%d")
        }
    }

    return {
        "ownership": ownership,
        "structure": structure,
        "classification": classification,
        "meta": meta,
    }
