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

    # Some wrappers return {"message": "..."} or {"error": "..."} on errors
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
    """
    Minimal evaluation using ZIP-level market stats (this API's capability).
    Returns:
      {
        "home":   { "fullAddress": "...", "zip": "32810" },
        "market": { "medianRent": ..., "temperature": "...", "raw": {...} },
        "comps":  []
      }
    """
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
        "raw": md,  # keep raw for debugging/expansion
    }

    return {
        "home": {"fullAddress": address, "zip": zip_code},
        "market": normalized_market,
        "comps": [],
    }


# ---- Property search + details (provider-agnostic via config) ----

def _p_cfg(name: str, default: str = "") -> str:
    # prefer Flask config, then env (same style as your _cfg)
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
    Try multiple zillow-com1-compatible ways to resolve an address -> zpid.

    ORDER:
      1) Explicit override path (if provided/configured)
      2) /propertyExtendedSearch  (location=<address>)
      3) /searchByUrl             (url=https://www.zillow.com/homes/<addr>_rb/)
      4) /locationSuggestions     (q=<address>) -> build searchByUrl from region url

    Returns a string zpid or None.
    """
    # 0) Setup
    chosen_host = host or _p_cfg("PROPERTY_HOST")
    headers = _headers(api_key, chosen_host)
    base = _provider_base(chosen_host)

    # Helper to pluck a zpid from a generic response
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

    # 1) custom path first (if configured)
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
    """
    Fetch detailed property facts by zpid/resourceId, mirroring:
      GET https://zillow-com1.p.rapidapi.com/property?zpid=<ID>
    """
    headers = _headers(api_key, host or _p_cfg("PROPERTY_HOST"))
    base = _provider_base(host or _p_cfg("PROPERTY_HOST"))
    path = (path_override or _p_cfg("PROPERTY_DETAILS_PATH", "/property"))
    url = f"{base}{path}"
    params = {"zpid": zpid}
    return _get(url, headers, params)


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
        # Common key names seen across providers
        parts = []
        street = val.get("fullAddress") or val.get("streetAddress") or val.get("address") or ""
        city = val.get("city") or ""
        state = val.get("state") or ""
        zipc = val.get("zipcode") or val.get("zip") or val.get("postalCode") or ""

        # Some providers nest under 'address'
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


def normalize_details(raw: dict) -> dict:
    """
    Map whatever the provider returns to a consistent shape your app expects.
    We look in common containers then pick common key names.
    Always flattens fullAddress to a string.
    """
    container = pick(raw, "home", "property", "data", default=raw if isinstance(raw, dict) else {})
    if not isinstance(container, dict):
        container = {}

    # Always flatten address
    addr_candidate = pick(container, "fullAddress", "address", "formattedAddress", "streetAddress")

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
    """
    One-stop helper:
      1) search address -> zpid
      2) fetch /property by zpid
      3) (optional) normalize shape
      4) (optional) attach ZIP-level market stats
    """
    zpid = search_address_for_zpid(address, api_key=api_key, host=host, path_override=search_path)
    if not zpid:
        raise ZillowError("No matching property/zpid found for that address.")

    raw = property_details_by_zpid(zpid, api_key=api_key, host=host, path_override=details_path)
    result: Dict[str, Any] = normalize_details(raw) if normalize else raw

    if include_market:
        try:
            zip_code = _extract_zip(address) or (
                isinstance(result, dict) and _extract_zip(result.get("fullAddress", ""))
            )
            if zip_code:
                md = evaluate_address_with_marketdata(address or (result.get("fullAddress", "") if isinstance(result, dict) else ""),
                                                      api_key=api_key, host=host)
                if isinstance(result, dict):
                    result["market"] = md.get("market") if isinstance(md, dict) else None
        except ZillowError:
            if isinstance(result, dict):
                result["market"] = None

    return result
