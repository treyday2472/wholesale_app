# app/services/zillow_client.py
from __future__ import annotations

import os
import re
import requests
from typing import Any, Dict, Optional, Tuple
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv

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


def _try_get(url: str, headers: Dict[str, str], params: Dict[str, Any]) -> Tuple[int, Optional[Dict[str, Any]]]:
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


def _extract_zip(address: str) -> Optional[str]:
    """Return a 5-digit ZIP if we can spot one in the address."""
    m = _ZIP_RE.search(address or "")
    return m.group(1) if m else None


def market_data_by_zip(zip_code: str,
                       api_key: Optional[str] = None,
                       host: Optional[str] = None) -> Dict[str, Any]:
    """Calls /marketData?resourceId=<ZIP> on the zillow-com1 API."""
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
    We try to pass: address, city, state, zipcode
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
    """
    Tries /walkAndTransitScore. Params vary by provider; we try lat/lng first.
    """
    if lat is None or lng is None:
        return None
    headers = _headers(api_key, host)
    base = f"https://{headers['x-rapidapi-host']}"
    url = f"{base}/walkAndTransitScore"
    status, data = _try_get(url, headers, {"latitude": lat, "longitude": lng})
    if status == 200:
        return data
    # fallback try alt param names
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
    """
    Convert provider address variants to a single-line string.
    Accepts str or dict-like with common keys.
    """
    if isinstance(val, str):
        return val.strip()

    if isinstance(val, dict):
        parts = []
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

        line = " ".join(parts).strip()
        return line or str(val)

    return str(val).strip()


def _address_bits(raw: dict) -> Dict[str, Any]:
    """
    Extract address parts useful for /rentEstimate
    """
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
    else:
        # We won't try to parse a single line; rely on zipcode in full string if needed.
        pass
    return bits


def normalize_details(raw: dict) -> dict:
    """
    Map whatever the provider returns to a consistent shape your app expects.
    Always flattens fullAddress to a string.
    """
    container = pick(raw, "home", "property", "data", default=raw if isinstance(raw, dict) else {})
    if not isinstance(container, dict):
        container = {}

    addr_candidate = pick(container, "fullAddress", "address", "formattedAddress", "streetAddress")
    home_type = pick(container, "homeType", "propertyType", "type", "useCode")
    units = pick(container, "unitCount", "units")

    return {
        "zpid": pick(container, "zpid", "id", "resourceId"),
        "fullAddress": _stringify_address(addr_candidate),
        "bedrooms": pick(container, "bedrooms", "beds"),
        "bathrooms": pick(container, "bathrooms", "baths"),
        "livingArea": pick(container, "livingArea", "sqft", "living_area"),
        "lotSize": pick(container, "lotAreaValue", "lotSize", "lot_size"),
        "yearBuilt": pick(container, "yearBuilt", "year_built"),
        "lat": pick(container, "latitude", "lat"),
        "lng": pick(container, "longitude", "lng"),
        "schoolDistrict": pick(container, "schoolDistrict", "district"),
        "homeType": home_type,
        "unitCount": units,
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
    elif any(s in t for s in ["duplex"]):
        category = "duplex"
    elif any(s in t for s in ["triplex"]):
        category = "triplex"
    elif any(s in t for s in ["quad", "fourplex", "four-plex"]):
        category = "fourplex"
    elif any(s in t for s in ["multi", "apartment", "condo", "townhouse", "townhome"]):
        category = "multifamily" if "multi" in t or (unit_count and unit_count > 1) else t

    return {"propertyTypeRaw": home_type, "classification": category, "unitCount": unit_count}


def _last_sale_from_history(hist: Dict[str, Any]) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """
    Try to extract last sale price/date and purchase method from /priceAndTaxHistory.
    """
    records = []
    for key in ("priceHistory", "data", "history", "events"):
        arr = hist.get(key)
        if isinstance(arr, list):
            records = arr
            break
    sale_price = None
    sale_date = None
    method = None
    # iterate newest -> oldest
    for rec in (records or []):
        etype = (rec.get("event") or rec.get("type") or "").lower()
        if "sold" in etype or etype == "sale" or "sale" in etype:
            sale_price = rec.get("price") or rec.get("amount")
            sale_date = rec.get("date")
            method = rec.get("source") or rec.get("buyerType") or rec.get("purchaseMethod")
            break
    return sale_price, sale_date, method


def investor_snapshot_by_zpid(zpid: str,
                              *,
                              api_key: Optional[str] = None,
                              host: Optional[str] = None,
                              include_market: bool = True) -> Dict[str, Any]:
    """
    Build an 'investor snapshot' dictionary that covers buckets 1–8.
    """
    # Base details
    raw_details = property_details_by_zpid(zpid, api_key=api_key, host=host)
    details = normalize_details(raw_details)
    addr_bits = _address_bits(raw_details)

    # 1) Ownership & mortgage info (limited by API)
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

    # 3) Valuation & income potential
    zdata = zestimate_by_zpid(zpid, api_key=api_key, host=host)
    zestimate_val = zdata.get("zestimate") if isinstance(zdata, dict) else None
    z_low = zdata.get("low") if isinstance(zdata, dict) else None
    z_high = zdata.get("high") if isinstance(zdata, dict) else None

    rdata = rent_estimate(addr_bits, api_key=api_key, host=host) or {}
    rent_val = rdata.get("rentZestimate") or rdata.get("rent") or rdata.get("estimate")

    valuation_income = {
        "zestimate": zestimate_val,
        "zestimateRange": {"low": z_low, "high": z_high},
        "rentEstimate": rent_val,
        "grossYield": (round(((rent_val or 0) * 12 / zestimate_val * 100), 2)
                       if (rent_val and zestimate_val and zestimate_val > 0) else None),
    }

    # 4) Lot & building specs (already in details)
    lot_building = {
        "lotSize": details.get("lotSize"),
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

    # 6) Marketability (limited w/out active listing context)
    marketability = {
        "daysOnMarket": None,  # not reliable unless tied to an active listing
        "previousListingHistory": None,
        "hoa": None,
        "rentalRestrictions": None,
    }

    # 7) Repair & rehab (requires manual input or MLS enrich)
    rehab = {
        "condition": None,
        "estimatedRehabCost": None,
        "photos": None,
    }

    # 8) Exit strategy flags (basic heuristics)
    exit_flags = {
        "flipPotential": None,    # needs comp/rehab logic
        "brrrrPotential": None,   # needs DSCR + rate assumptions
        "wholesaleSpread": None,  # needs seller ask/contract price
        "creativeFinance": None,  # needs mortgage data
    }

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
        "details": details,   # include normalized details
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
    """
    Address -> zpid -> snapshot
    """
    zpid = search_address_for_zpid(address, api_key=api_key, host=host)
    if not zpid:
        raise ZillowError("No matching property/zpid found for that address.")
    return investor_snapshot_by_zpid(zpid, api_key=api_key, host=host, include_market=include_market)
