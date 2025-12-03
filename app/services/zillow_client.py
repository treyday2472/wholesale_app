from __future__ import annotations

import os
import re
import requests
from typing import Any, Dict, Optional, Tuple, List
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv
from flask import current_app

load_dotenv()


try:
    from flask import current_app  # available when inside app context
except Exception:
    current_app = None

# ---- load .env (prefer project-root .env/.env.txt and override OS vars) ----
_THIS_FILE = Path(__file__).resolve()
_PROJECT_ROOT = _THIS_FILE.parents[2]
for candidate in (_PROJECT_ROOT / ".env", _PROJECT_ROOT / ".env.txt"):
    if candidate.exists():
        load_dotenv(dotenv_path=str(candidate), override=True)
        break


class ZillowError(Exception):
    pass


def _cfg(name: str) -> str:
    """Prefer Flask config; fallback to environment."""
    try:
        if current_app is not None:
            val = current_app.config.get(name)
            if isinstance(val, str) and val.strip():
                return val.strip()
    except Exception:
        pass
    return os.getenv(name, "").strip()


def _headers(api_key: Optional[str] = None, host: Optional[str] = None) -> Dict[str, str]:
    key = (api_key or _cfg("RAPIDAPI_KEY")).strip()
    if not key:
        raise ZillowError("Missing RAPIDAPI_KEY.")
    h = (host or _cfg("ZILLOW_HOST") or "zillow-com1.p.rapidapi.com").strip()
    return {
        "x-rapidapi-key": key,
        "x-rapidapi-host": h,
        "accept": "application/json",
    }


def _get(url: str, headers: Dict[str, str], params: Dict[str, Any]) -> Dict[str, Any]:
    try:
        r = requests.get(url, headers=headers, params=params, timeout=(6, 20))
    except requests.RequestException as e:
        raise ZillowError(f"Network error calling Zillow: {e}") from e

    if r.status_code == 401:
        raise ZillowError("Unauthorized (401). Check RAPIDAPI_KEY.")
    if r.status_code == 403:
        raise ZillowError("Forbidden (403). Check plan/host or headers.")
    if r.status_code == 404:
        raise ZillowError("Not found (404).")
    if r.status_code >= 500:
        raise ZillowError(f"Zillow server error ({r.status_code}).")

    try:
        data = r.json()
    except ValueError:
        raise ZillowError("Response was not JSON.")

    if isinstance(data, dict):
        msg = data.get("message") or data.get("error")
        if msg and r.status_code != 200:
            raise ZillowError(f"Zillow API error: {msg}")

    return data


def _try_get(url: str, headers: Dict[str, str], params: Dict[str, Any]) -> Tuple[int, Any]:
    """Like _get but never raises; returns (status_code, data_or_none)."""
    try:
        r = requests.get(url, headers=headers, params=params, timeout=(6, 20))
        status = r.status_code
        try:
            data = r.json()
        except Exception:
            data = None
        return status, data
    except requests.RequestException:
        return 599, None


# ---------------- ZIP-based market data (works with zillow-com1) ----------------

_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


# Prefer the trailing 5-digit ZIP in the string
def _extract_zip(address: str) -> Optional[str]:
    if not address:
        return None
    matches = _ZIP_RE.findall(address)
    # _ZIP_RE has a capturing group; findall() returns just that group
    return matches[-1] if matches else None


def market_data_by_zip(zip_code: str,
                       api_key: Optional[str] = None,
                       host: Optional[str] = None) -> Dict[str, Any]:
    headers = _headers(api_key, host)
    base = f"https://{headers['x-rapidapi-host']}"
    url = f"{base}/marketData"
    return _get(url, headers, {"resourceId": zip_code})


def evaluate_address_with_marketdata(address: str,
                                     api_key: Optional[str] = None,
                                     host: Optional[str] = None) -> Dict[str, Any]:
    zip_code = _extract_zip(address)
    if not zip_code:
        raise ZillowError("Could not determine ZIP from the address. Include a 5-digit ZIP.")

    md = market_data_by_zip(zip_code, api_key, host)
    summary = md.get("summary") or {}
    temp = (md.get("marketTemperature") or {}).get("temperature")

    normalized_market = {
        "medianRent": summary.get("medianRent"),
        "avgDaysOnMarket": summary.get("avgDaysOnMarket"),
        "availableRentals": summary.get("availableRentals"),
        "monthlyChange": summary.get("monthlyChange"),
        "yearlyChange": summary.get("yearlyChange"),
        "temperature": temp,
        "raw": md,
    }

    return {
        "home": {"fullAddress": address, "zip": zip_code},
        "market": normalized_market,
        "comps": [],
    }


# ---- Property search + details (provider-agnostic via config) ----

def _p_cfg(name: str, default: str = "") -> str:
    try:
        from flask import current_app as _ca
        val = (_ca.config.get(name) or os.getenv(name, default)).strip()
    except Exception:
        val = os.getenv(name, default).strip()
    return val or default


def _provider_base(host_override: Optional[str] = None) -> str:
    host = (host_override or _p_cfg("PROPERTY_HOST") or _p_cfg("ZILLOW_HOST") or "zillow-com1.p.rapidapi.com").strip()
    return f"https://{host}"


def search_address_for_zpid(address: str,
                            api_key: Optional[str] = None,
                            host: Optional[str] = None,
                            path_override: Optional[str] = None) -> Optional[str]:
    """
    Resolve an address -> zpid with multiple fallbacks.
    """
    chosen_host = host or _p_cfg("PROPERTY_HOST")
    headers = _headers(api_key, chosen_host)
    base = _provider_base(chosen_host)

    def _pluck_zpid(data: Any) -> Optional[str]:
        if isinstance(data, dict):
            for key in ("data", "results", "properties", "items", "props", "list"):
                arr = data.get(key)
                if isinstance(arr, list) and arr:
                    cand = arr[0]
                    if isinstance(cand, dict):
                        z = cand.get("zpid") or cand.get("resourceId") or cand.get("id")
                        if z:
                            return str(z)
        if isinstance(data, list) and data:
            cand = data[0]
            if isinstance(cand, dict):
                z = cand.get("zpid") or cand.get("resourceId") or cand.get("id")
                if z:
                    return str(z)
        if isinstance(data, dict):
            z = data.get("zpid") or (data.get("home") or {}).get("zpid")
            if z:
                return str(z)
        return None

    # 1) custom
    path = (path_override or _p_cfg("PROPERTY_SEARCH_PATH", "")).strip()
    if path:
        url = f"{base}{path}"
        status, data = _try_get(url, headers, {"term": address})
        if status == 200:
            zpid = _pluck_zpid(data)
            if zpid:
                return zpid

    # 2) propertyExtendedSearch
    url = f"{base}/propertyExtendedSearch"
    status, data = _try_get(url, headers, {"location": address})
    if status == 200:
        zpid = _pluck_zpid(data)
        if zpid:
            return zpid

    # 3) searchByUrl
    url = f"{base}/searchByUrl"
    z_url = f"https://www.zillow.com/homes/{quote(address)}_rb/"
    status, data = _try_get(url, headers, {"url": z_url})
    if status == 200:
        zpid = _pluck_zpid(data)
        if zpid:
            return zpid

    # 4) locationSuggestions -> region url -> searchByUrl
    url = f"{base}/locationSuggestions"
    status, data = _try_get(url, headers, {"q": address})
    if status == 200 and isinstance(data, dict):
        suggs = data.get("results") or data.get("suggestions") or data.get("data") or []
        if isinstance(suggs, list) and suggs:
            first = suggs[0]
            region_url = (first.get("url") or first.get("path") or "").strip()
            if region_url:
                if region_url.startswith("/"):
                    region_url = "https://www.zillow.com" + region_url
                status2, data2 = _try_get(f"{base}/searchByUrl", headers, {"url": region_url})
                if status2 == 200:
                    zpid = _pluck_zpid(data2)
                    if zpid:
                        return zpid

    return None


def property_details_by_zpid(zpid: str,
                             api_key: Optional[str] = None,
                             host: Optional[str] = None,
                             path_override: Optional[str] = None) -> Dict[str, Any]:
    headers = _headers(api_key, host or _p_cfg("PROPERTY_HOST"))
    base = _provider_base(host or _p_cfg("PROPERTY_HOST"))
    path = (path_override or _p_cfg("PROPERTY_DETAILS_PATH", "/property"))
    url = f"{base}{path}"
    params = {"zpid": zpid}
    return _get(url, headers, params)

def get_zillow_details(
    zpid: str,
    *,
    api_key: Optional[str] = None,
    host: Optional[str] = None,
    normalize: bool = False
) -> Dict[str, Any]:
    """
    Convenience helper:
      - calls property_details_by_zpid()
      - optionally normalizes to your standard detail schema.
    """
    raw = property_details_by_zpid(zpid, api_key=api_key, host=host)

    if normalize:
        return normalize_details(raw)

    return raw


# ---- Extra endpoints we need for 1–8 ----

def price_and_tax_history_by_zpid(zpid: str,
                                  api_key: Optional[str] = None,
                                  host: Optional[str] = None) -> Dict[str, Any]:
    headers = _headers(api_key, host)
    base = f"https://{headers['x-rapidapi-host']}"
    url = f"{base}/priceAndTaxHistory"
    return _get(url, headers, {"zpid": zpid})


def zestimate_by_zpid(zpid: str,
                      api_key: Optional[str] = None,
                      host: Optional[str] = None) -> Dict[str, Any]:
    headers = _headers(api_key, host)
    base = f"https://{headers['x-rapidapi-host']}"
    url = f"{base}/zestimate"
    return _get(url, headers, {"zpid": zpid})


def rent_estimate(address_bits: Dict[str, Any],
                  api_key: Optional[str] = None,
                  host: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    /rentEstimate usually needs address fields, not zpid.
    """
    headers = _headers(api_key, host)
    base = f"https://{headers['x-rapidapi-host']}"
    url = f"{base}/rentEstimate"

    addr = address_bits.get("street") or address_bits.get("streetAddress") or address_bits.get("line")
    city = address_bits.get("city")
    state = address_bits.get("state")
    zipc = address_bits.get("zipcode") or address_bits.get("postalCode") or address_bits.get("zip")

    if not (addr and city and state and zipc):
        return None

    status, data = _try_get(url, headers, {
        "address": addr, "city": city, "state": state, "zipcode": zipc
    })
    return data if status == 200 else None


def walk_transit_scores(lat: Optional[float], lng: Optional[float],
                        api_key: Optional[str] = None,
                        host: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if lat is None or lng is None:
        return None
    headers = _headers(api_key, host)
    base = f"https://{headers['x-rapidapi-host']}"
    url = f"{base}/walkAndTransitScore"
    status, data = _try_get(url, headers, {"latitude": lat, "longitude": lng})
    if status == 200:
        return data
    status2, data2 = _try_get(url, headers, {"lat": lat, "lon": lng})
    return data2 if status2 == 200 else None


# ---- Normalize helpers ----

def pick(obj: dict, *keys, default=None):
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else None
        if v not in (None, "", []):
            return v
    return default


def _stringify_address(val: Any) -> str:
    if isinstance(val, str):
        return val.strip()

    if isinstance(val, dict):
        parts: List[str] = []
        street = val.get("fullAddress") or val.get("streetAddress") or val.get("address") or ""
        city = val.get("city") or ""
        state = val.get("state") or ""
        zipc = val.get("zipcode") or val.get("zip") or val.get("postalCode") or ""

        nested = val.get("address")
        if not street and isinstance(nested, dict):
            street = nested.get("streetAddress") or nested.get("line") or street
            city = city or nested.get("city", "")
            state = state or nested.get("state", "")
            zipc = zipc or nested.get("postalCode", "")

        if street:
            parts.append(str(street).strip())
        city_state = ", ".join(p for p in [str(city).strip(), str(state).strip()] if p)
        if city_state:
            parts.append(city_state)
        if zipc:
            parts.append(str(zipc).strip())

        return " ".join(parts).strip() or str(val)

    return str(val).strip()


def _address_bits(raw: dict) -> Dict[str, Any]:
    container = pick(raw, "home", "property", "data", default=raw if isinstance(raw, dict) else {})
    if not isinstance(container, dict):
        container = {}
    addr = pick(container, "address", "fullAddress", "formattedAddress", "streetAddress")
    bits: Dict[str, Any] = {}
    if isinstance(addr, dict):
        bits["street"] = addr.get("streetAddress") or addr.get("fullAddress") or addr.get("line")
        bits["city"] = addr.get("city")
        bits["state"] = addr.get("state")
        bits["zipcode"] = addr.get("zipcode") or addr.get("postalCode") or addr.get("zip")
    return bits


def _to_float(val) -> Optional[float]:
    try:
        return float(str(val).replace(",", "").strip())
    except Exception:
        return None


_ACRE_PAT = re.compile(r"([\d\.,]+)\s*(acre|acres|ac)\b", re.I)
_SQFT_PAT = re.compile(r"([\d\.,]+)\s*(sq\s*ft|sqft|square\s*feet)\b", re.I)


def _lot_from_freeform(text: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Parse strings like '0.37 Acres' or '16,117 sqft' and return (sqft, pretty_str).
    """
    if not text:
        return None, None
    t = text.strip()

    m = _ACRE_PAT.search(t)
    if m:
        num = _to_float(m.group(1))
        if num is not None:
            sqft = int(round(num * 43560))
            return sqft, f"{num:g} ac"

    m2 = _SQFT_PAT.search(t)
    if m2:
        num = _to_float(m2.group(1))
        if num is not None:
            sqft = int(round(num))
            return sqft, f"{sqft:,} sqft"

    # pure number? heuristics
    num = _to_float(t)
    if num is not None:
        if num < 1000:  # likely acres
            sqft = int(round(num * 43560))
            return sqft, f"{num:g} ac"
        sqft = int(round(num))
        return sqft, f"{sqft:,} sqft"

    return None, None


def _score_district_name(name: str) -> int:
    """
    Rough heuristic: prefer strings that look like districts over single schools.
    """
    n = (name or "").lower()
    score = 0
    if any(k in n for k in ["school district", "isd", "independent sd", "unified", "public schools", "district"]):
        score += 5
    if any(k in n for k in ["elementary", "middle", "high", "school"]):
        score -= 2  # likely a specific school name
    score += max(0, len(n) // 10)  # longer tends to be district-level
    return score


def _lot_fields(container: dict) -> Tuple[Optional[int], Optional[str]]:
    """
    Return (lot_sqft_numeric, lot_display_str).
    Covers:
      - resoFacts.atAGlanceFacts["Lot"|"Lot Size"|"Lot Area"]
      - lotAreaValue + lotAreaUnit(s)
      - lotSize{SquareFeet,Acres} numeric
      - lotSize freeform text '0.37 Acres', '16,117 sqft'
    """
    # 0) resoFacts.atAGlanceFacts -> "Lot"
    rf = container.get("resoFacts") or {}
    gl = rf.get("atAGlanceFacts") or []
    for f in gl:
        if isinstance(f, dict):
            label = (f.get("factLabel") or "").strip().lower()
            if label in ("lot", "lot size", "lot area"):
                sqft, disp = _lot_from_freeform(str(f.get("factValue") or ""))
                if sqft:
                    return sqft, disp

    # 1) canonical pair
    val = container.get("lotAreaValue")
    unit = (container.get("lotAreaUnit") or container.get("lotAreaUnits") or "").lower().strip()
    if val is not None:
        num = _to_float(val)
        if num is not None:
            if unit.startswith("ac"):
                sqft = int(round(num * 43560))
                return sqft, f"{num:g} ac"
            # if no unit but small number, assume acres
            if not unit and num < 1000:
                sqft = int(round(num * 43560))
                return sqft, f"{num:g} ac"
            sqft = int(round(num))
            return sqft, f"{sqft:,} sqft"

    # 2) numeric sqft alternates
    for k in ("lotSizeSquareFeet", "lot_size_sqft", "lotAreaSqft", "lot_size"):
        num = _to_float(container.get(k))
        if num is not None:
            sqft = int(round(num))
            return sqft, f"{sqft:,} sqft"

    # 3) numeric acres alternates
    for k in ("lotSizeAcres", "lot_size_acres", "lotAreaAcres"):
        num = _to_float(container.get(k))
        if num is not None:
            sqft = int(round(num * 43560))
            return sqft, f"{num:g} ac"

    # 4) freeform strings
    for k in ("lotSize", "lot_area", "lotArea", "lotSizeText"):
        v = container.get(k)
        if isinstance(v, str) and v.strip():
            sqft, disp = _lot_from_freeform(v)
            if sqft:
                return sqft, disp

    return None, None


def _extract_school_district(container: dict) -> Optional[str]:
    # Direct district-named fields
    for k in ("elementarySchoolDistrict", "middleOrJuniorSchoolDistrict", "highSchoolDistrict"):
        v = container.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()

    candidates: List[str] = []

    for k in ("schoolDistrict", "district", "districtName"):
        v = container.get(k)
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())

    schools = container.get("schools")
    if isinstance(schools, dict):
        for v in schools.values():
            if isinstance(v, dict):
                for kk in ("district", "districtName"):
                    vv = v.get(kk)
                    if isinstance(vv, str) and vv.strip():
                        candidates.append(vv.strip())
    elif isinstance(schools, list):
        for item in schools:
            if isinstance(item, dict):
                for kk in ("district", "districtName"):
                    vv = item.get(kk)
                    if isinstance(vv, str) and vv.strip():
                        candidates.append(vv.strip())

    nearby = container.get("nearbySchools")
    if isinstance(nearby, list):
        for item in nearby:
            if isinstance(item, dict):
                for kk in ("district", "districtName"):
                    vv = item.get(kk)
                    if isinstance(vv, str) and vv.strip():
                        candidates.append(vv.strip())

    if candidates:
        # de-dup and prefer district-like names
        uniq = list(dict.fromkeys(candidates))
        uniq.sort(key=_score_district_name, reverse=True)
        return uniq[0]
    return None


def normalize_details(raw: dict) -> dict:
    """
    Map provider response to a consistent shape.
    Produces both numeric lotSize (sqft) AND a display string.
    Extracts a district (not a specific school).
    """
    container = pick(raw, "home", "property", "data", default=raw if isinstance(raw, dict) else {})
    if not isinstance(container, dict):
        container = {}

    addr_candidate = pick(container, "fullAddress", "address", "formattedAddress", "streetAddress")

    lot_sqft, lot_disp = _lot_fields(container)
    school_district = _extract_school_district(container)

    return {
        "zpid": pick(container, "zpid", "id", "resourceId"),
        "fullAddress": _stringify_address(addr_candidate),
        "bedrooms": pick(container, "bedrooms", "beds"),
        "bathrooms": pick(container, "bathrooms", "baths"),
        "livingArea": pick(container, "livingArea", "livingAreaValue", "sqft", "living_area"),

        # Lot: numeric + display
        "lotSize": lot_sqft,
        "lotSizeDisplay": lot_disp,

        "yearBuilt": pick(container, "yearBuilt", "year_built"),
        "lat": pick(container, "latitude", "lat"),
        "lng": pick(container, "longitude", "lng"),

        "schoolDistrict": school_district,

        "homeType": pick(container, "homeType", "propertyType", "propertyTypeDimension", "type", "useCode"),
        "unitCount": pick(container, "unitCount", "units"),

        "raw": raw,
    }


# ---------------- Convenience: address -> details (+ optional market) -----------

def address_to_details(address: str,
                       *,
                       api_key: Optional[str] = None,
                       host: Optional[str] = None,
                       search_path: Optional[str] = None,
                       details_path: Optional[str] = None,
                       normalize: bool = True,
                       include_market: bool = False) -> Dict[str, Any]:
    zpid = search_address_for_zpid(address, api_key=api_key, host=host, path_override=search_path)
    if not zpid:
        raise ZillowError("No matching property/zpid found for that address.")

    raw = property_details_by_zpid(zpid, api_key=api_key, host=host, path_override=details_path)
    result: Dict[str, Any] = normalize_details(raw) if normalize else raw

    if include_market and isinstance(result, dict):
        try:
            zip_code = _extract_zip(address) or _extract_zip(result.get("fullAddress", ""))
            if zip_code:
                md = evaluate_address_with_marketdata(address or result.get("fullAddress", ""), api_key=api_key, host=host)
                result["market"] = md.get("market") if isinstance(md, dict) else None
        except ZillowError:
            result["market"] = None

    return result


# ---------------- Investor snapshot (covers your 1–8) --------------------------

def _classify_type(home_type: Optional[str], unit_count: Optional[int]) -> Dict[str, Any]:
    t = (home_type or "").lower()
    category = "unknown"
    if any(s in t for s in ["single", "sfr", "house", "detached"]):
        category = "sfr"
    elif "duplex" in t:
        category = "duplex"
    elif "triplex" in t:
        category = "triplex"
    elif any(s in t for s in ["quad", "fourplex", "four-plex"]):
        category = "fourplex"
    elif any(s in t for s in ["multi", "apartment", "condo", "townhouse", "townhome"]):
        category = "multifamily" if "multi" in t or (unit_count and unit_count > 1) else t
    return {"propertyTypeRaw": home_type, "classification": category, "unitCount": unit_count}


def _last_sale_from_history(hist: Dict[str, Any]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    records = []
    for key in ("priceHistory", "data", "history", "events"):
        arr = hist.get(key)
        if isinstance(arr, list):
            records = arr
            break
    sale_price = None
    sale_date = None
    method = None
    for rec in (records or []):  # newest->oldest is typical
        etype = (rec.get("event") or rec.get("type") or "").lower()
        if "sold" in etype or etype == "sale" or "sale" in etype:
            sale_price = rec.get("price") or rec.get("amount")
            sale_date = rec.get("date")
            method = rec.get("source") or rec.get("buyerType") or rec.get("purchaseMethod")
            break
    return sale_price, sale_date, method

def comps_by_zpid(
    zpid: str,
    api_key: Optional[str] = None,
    host: Optional[str] = None,
    path_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Low-level wrapper for the Zillow/RapidAPI comps endpoint.
    Uses PROPERTY_COMPS_PATH if set; default '/propertyComps'.
    """
    resolved_host = host or _p_cfg("PROPERTY_HOST")
    headers = _headers(api_key, resolved_host)
    base = _provider_base(resolved_host)
    path = (path_override or _p_cfg("PROPERTY_COMPS_PATH", "/propertyComps")).strip()  # adjust if your RapidAPI path differs
    url = f"{base}{path}"

    params = {
        "zpid": zpid,
        "count": 10,  # tweak as needed
    }
    return _get(url, headers, params)


def normalize_comps(raw: Any) -> List[Dict[str, Any]]:
    """
    Normalize various provider shapes into a simple comps list.
    Each comp will have the fields the AI & UI care about:
      zpid, address, beds, baths, sqft, list_price, sale_price,
      zestimate, status, sale_date, url, distance
    """
    if isinstance(raw, dict):
        items = (
            raw.get("comparables")
            or raw.get("comps")
            or raw.get("properties")
            or raw.get("data")
            or raw.get("results")
            or []
        )
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    comps: List[Dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        home = item.get("home") or item.get("property") or item

        addr_candidate = pick(
            home, "fullAddress", "address", "formattedAddress", "streetAddress"
        )

        zpid = pick(home, "zpid", "id", "resourceId")

        # build a "best guess" URL if nothing provided
        url = (
            home.get("hdpUrl")
            or home.get("zillowUrl")
            or home.get("url")
            or (f"https://www.zillow.com/homedetails/{zpid}_zpid/" if zpid else None)
        )

        comp = {
            "zpid": zpid,
            "address": _stringify_address(addr_candidate),
            "beds": pick(home, "bedrooms", "beds"),
            "baths": pick(home, "bathrooms", "baths"),
            "sqft": pick(home, "livingArea", "livingAreaValue", "sqft", "living_area"),
            "list_price": pick(home, "price", "unformattedPrice", "listPrice"),
            "sale_price": pick(
                home,
                "salePrice",
                "soldPrice",
                "lastSalePrice",
                "amount",
            ),
            "zestimate": home.get("zestimate"),
            "status": pick(home, "homeStatus", "statusType", "listingStatus"),
            "sale_date": pick(home, "soldDate", "saleDate", "date"),
            "distance": pick(item, "distance", "distanceInMeters", "distanceInKm"),
            "url": url,
            "raw": item,
        }

        comps.append(comp)

    return comps


def get_comps_for_zpid(
    zpid: str,
    *,
    api_key: Optional[str] = None,
    host: Optional[str] = None,
    normalize: bool = True,
) -> Any:
    """
    Main entry point: subject zpid -> comps list.

    - If normalize=True (default): returns a list of normalized comp dicts.
    - If normalize=False: returns raw provider payload.
    """
    raw = comps_by_zpid(zpid, api_key=api_key, host=host)
    return normalize_comps(raw) if normalize else raw



def investor_snapshot_by_zpid(zpid: str,
                              *,
                              api_key: Optional[str] = None,
                              host: Optional[str] = None,
                              include_market: bool = True) -> Dict[str, Any]:
    raw_details = property_details_by_zpid(zpid, api_key=api_key, host=host)
    details = normalize_details(raw_details)
    addr_bits = _address_bits(raw_details)
    from .enrichers import enrich_details
    # ...
    details = normalize_details(raw_details)

    # Enrich missing bits
    details = enrich_details(details)

    # 1) Ownership & mortgage info
    pth = price_and_tax_history_by_zpid(zpid, api_key=api_key, host=host)
    last_price, last_date, purchase_method = _last_sale_from_history(pth)
    ownership_mortgage = {
        "lastSoldPrice": last_price,
        "lastSoldDate": last_date,
        "purchaseMethod": purchase_method,
        "mortgageAmount": None,
        "mortgageOriginationDate": None,
        "mortgageType": None,
        "lender": None,
        "ownerOccupied": None,
    }

    # 2) Property classification
    classification = _classify_type(details.get("homeType"), details.get("unitCount"))

    # 3) Valuation & income
    zdata = zestimate_by_zpid(zpid, api_key=api_key, host=host)
    zestimate_val = zdata.get("zestimate") if isinstance(zdata, dict) else None
    z_low = zdata.get("low") if isinstance(zdata, dict) else None
    z_high = zdata.get("high") if isinstance(zdata, dict) else None

    rdata = rent_estimate(addr_bits, api_key=api_key, host=host) or {}
    rent_val = (rdata.get("rent") or rdata.get("rentZestimate") or rdata.get("amount") 
                or rdata.get("value") or details.get("rentEstimate"))

    valuation_income = {
        "zestimate": zestimate_val,
        "zestimateRange": {"low": z_low, "high": z_high},
        "rentEstimate": rent_val,
        "grossYield": (round(((rent_val or 0) * 12 / zestimate_val * 100), 2)
                    if (rent_val and zestimate_val and zestimate_val > 0) else None),
    }


    # 4) Lot & building
    lot_building = {
        "lotSize": details.get("lotSize"),
        "lotSizeDisplay": details.get("lotSizeDisplay"),
        "livingArea": details.get("livingArea"),
        "yearBuilt": details.get("yearBuilt"),
        "constructionType": None,
        "majorSystemsAge": {"roof": None, "hvac": None, "waterHeater": None},
    }

    # 5) Location desirability
    scores = walk_transit_scores(details.get("lat"), details.get("lng"), api_key=api_key, host=host) or {}
    location_desirability = {
        "schoolDistrict": details.get("schoolDistrict"),
        "walkTransit": scores,
        "crimeRating": None,
        "keyAmenities": None,
    }

    # 6) Marketability (limited without active listing)
    marketability = {
        "daysOnMarket": None,
        "previousListingHistory": None,
        "hoa": None,
        "rentalRestrictions": None,
    }

    # 7) Repair & rehab
    rehab = {"condition": None, "estimatedRehabCost": None, "photos": None}

    # 8) Exit flags (placeholder heuristics)
    exit_flags = {"flipPotential": None, "brrrrPotential": None, "wholesaleSpread": None, "creativeFinance": None}

    snapshot: Dict[str, Any] = {
        "meta": {"zpid": details.get("zpid"), "fullAddress": details.get("fullAddress")},
        "1_ownership_mortgage": ownership_mortgage,
        "2_classification": classification,
        "3_valuation_income": valuation_income,
        "4_lot_building": lot_building,
        "5_location_desirability": location_desirability,
        "6_marketability": marketability,
        "7_rehab": rehab,
        "8_exit_flags": exit_flags,
        "details": details,
    }

    if include_market:
        try:
            fa = details.get("fullAddress", "")
            md = evaluate_address_with_marketdata(fa, api_key=api_key, host=host)
            snapshot["market"] = md.get("market")
        except ZillowError:
            snapshot["market"] = None

    return snapshot


def investor_snapshot_by_address(address: str,
                                 *,
                                 api_key: Optional[str] = None,
                                 host: Optional[str] = None,
                                 include_market: bool = True) -> Dict[str, Any]:
    zpid = search_address_for_zpid(address, api_key=api_key, host=host)
    if not zpid:
        raise ZillowError("No matching property/zpid found for that address.")
    return investor_snapshot_by_zpid(zpid, api_key=api_key, host=host, include_market=include_market)


# app/services/zillow_client.py
from flask import current_app

# zillow_client.py

def zillow_basics(full_address: str, rapid_key: str | None = None, host: str | None = None) -> dict:
    rapid_key = (
        rapid_key
        or (current_app.config.get("RAPIDAPI_KEY") if current_app else None)
        or os.getenv("RAPIDAPI_KEY", "")
    )
    host = (
        host
        or (current_app.config.get("ZILLOW_HOST") if current_app else None)
        or os.getenv("ZILLOW_HOST", "zillow-com1.p.rapidapi.com")
    )

    # 1) Resolve zpid (this returns a STRING or None)
    try:
        zpid = search_address_for_zpid(full_address, api_key=rapid_key, host=host)
    except Exception:
        zpid = None

    # define basics BEFORE any .update() calls
    basics: dict = {"zpid": zpid, "raw": {}}

    # 2) Fallback: no zpid -> try market ZIP data so the UI isn't empty
    if not zpid:
        try:
            ev = evaluate_address_with_marketdata(full_address, api_key=rapid_key, host=host) or {}
        except Exception:
            ev = {}

        home = (ev.get("home") or ev.get("property") or ev.get("result") or {})
        def _pick(*candidates):
            for c in candidates:
                if c is not None:
                    return c
            return None

        zestimate = _pick(
            home.get("zestimate"),
            (home.get("zestimateData") or {}).get("amount"),
            (home.get("zestimateData") or {}).get("value"),
            (ev.get("zestimate") or {}).get("amount"),
            ev.get("zestimate"),
        )
        rent_zestimate = _pick(
            home.get("rentZestimate"),
            home.get("rent_zestimate"),
            (home.get("rent") or {}).get("amount"),
            (home.get("rent") or {}).get("value"),
            (ev.get("rent") or {}).get("amount"),
            ev.get("rent_zestimate"),
        )

        basics.update({
            "zestimate": zestimate,
            "rent_zestimate": rent_zestimate,
            "zillow_url": home.get("hdpUrl") or home.get("zillowUrl") or home.get("url"),
            "for_sale": str(home.get("homeStatus") or "").upper() in {"FOR_SALE", "PENDING", "ACTIVE"},
            "sale_status": home.get("homeStatus") or home.get("statusType"),
            "list_price": home.get("price") or home.get("unformattedPrice") or home.get("listPrice"),
            "raw": {"home": home},
        })
        return basics

    # 3) We have a zpid → pull full details and enrich
    d = property_details_by_zpid(zpid, api_key=rapid_key, host=host) or {}
    home = (d.get("home") or d.get("property") or d.get("homeDetails") or d)

    status = (home.get("homeStatus") or home.get("statusType") or "").upper()
    for_sale = bool(
        status in ("FOR_SALE", "PENDING", "ACTIVE")
        or home.get("isForSale")
        or home.get("isFsbo")
        or home.get("isNewConstruction")
    )

    canonical_url = f"https://www.zillow.com/homedetails/{zpid}_zpid/"

    basics.update({
        "zestimate": home.get("zestimate"),
        "rent_zestimate": home.get("rentZestimate") or home.get("rent_zestimate"),
        "zillow_url": home.get("hdpUrl") or home.get("zillowUrl") or home.get("url") or canonical_url,
        "url":        home.get("hdpUrl") or home.get("zillowUrl") or home.get("url") or canonical_url,  # your UI uses `url`
        "for_sale":   for_sale,
        "sale_status": home.get("homeStatus") or home.get("statusType"),
        "list_price": home.get("price") or home.get("unformattedPrice") or home.get("listPrice"),

        # Fill previously-null fields for your Show Key
        "beds":  home.get("bedrooms") or home.get("beds"),
        "baths": home.get("bathrooms") or home.get("baths"),
        "sqft":  home.get("livingArea") or home.get("living_area"),
        "year_built": home.get("yearBuilt") or home.get("year_built"),
        "photos": home.get("photos") or [],
        "raw": {"home": home},
    })

    return basics