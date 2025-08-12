# app/services/enrichers.py
from __future__ import annotations
import os
import re
import requests
from typing import Any, Dict, Optional, Tuple

# ---- helpers ---------------------------------------------------------------

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

# ---- SCHOOL DISTRICT (SchoolDigger) ---------------------------------------
# Sign up for keys: SCHOOLDIGGER_APP_ID, SCHOOLDIGGER_APP_KEY
# Docs: confirm exact params in their docs; this function is written so you
# can fill the correct endpoint/params when you add keys.

def school_district_from_schooldigger(*, address:str=None, city:str=None, state:str=None,
                                      lat:Optional[float]=None, lng:Optional[float]=None) -> Tuple[Optional[str], Optional[str]]:
    app_id  = _cfg("SCHOOLDIGGER_APP_ID")
    app_key = _cfg("SCHOOLDIGGER_APP_KEY")
    if not (app_id and app_key):
        return None, None

    try:
        # Option A: search districts by lat/lng (preferred if supported)
        # TODO: verify endpoint/params in SchoolDigger docs and adjust here.
        if lat is not None and lng is not None:
            url = "https://api.schooldigger.com/v2.0/districts"
            params = {
                "lat": lat, "lng": lng, "distance": 5,    # or suitable radius
                "appID": app_id, "appKey": app_key
            }
            r = requests.get(url, params=params, timeout=(5, 15))
            if r.ok:
                data = r.json()
                # pick the closest/highest match
                items = data.get("districts") or data.get("data") or []
                if items:
                    name = _first(items[0].get("districtName"), items[0].get("name"))
                    return (name, "schooldigger")

        # Option B: fallback by city/state text query
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

# ---- WALK / TRANSIT SCORE (official) --------------------------------------
# WALK_SCORE_API_KEY required.
# https://www.walkscore.com/professional/api.php

def walk_transit_from_walkscore(*, address:str, lat:float, lng:float) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    key = _cfg("WALK_SCORE_API_KEY")
    if not key or lat is None or lng is None or not address:
        return None, None
    try:
        # Walk Score
        ws = None
        url_ws = "https://api.walkscore.com/score"
        params_ws = {"format": "json", "address": address, "lat": lat, "lon": lng, "wsapikey": key}
        r1 = requests.get(url_ws, params=params_ws, timeout=(5, 15))
        if r1.ok:
            j = r1.json()
            if isinstance(j, dict) and _ok(j.get("walkscore")):
                ws = {"walkScore": j.get("walkscore")}

        # Transit score (if enabled for market)
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
        return (result or None, "walkscore")  # None if neither present
    except requests.RequestException:
        return None, None

# ---- RENT ESTIMATE (RentCast) ---------------------------------------------
# RENTCAST_API_KEY optional.
# https://www.rentcast.io (simple address-based rent estimate)

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
            # map provider shape → a single monthly estimate if available
            est = _first(j.get("rent"), j.get("rentEstimate"), j.get("estimate"))
            if _ok(est):
                try:
                    return int(round(float(est))), "rentcast"
                except Exception:
                    return None, None
    except requests.RequestException:
        pass
    return None, None

# ---- ENRICH ORCHESTRATOR ---------------------------------------------------

def enrich_details(details: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fills missing fields using external providers and returns a shallow
    copy with a 'provenance' map:
      details["provenance"] = { "schoolDistrict": "schooldigger", ... }
    """
    d = dict(details or {})
    provenance: Dict[str, str] = dict(d.get("provenance") or {})

    # Canonical address parts if we have raw bits
    fulladdr = d.get("fullAddress")
    lat, lng = d.get("lat"), d.get("lng")
    raw = d.get("raw") or {}
    rf = (raw.get("resoFacts") if isinstance(raw, dict) else None) or {}
    addr_bits = {
        "address": _first(rf.get("streetAddress"), raw.get("streetAddress")),
        "city":    _first(rf.get("city"), raw.get("city")),
        "state":   _first(rf.get("state"), raw.get("state")),
        "zip":     _first(rf.get("zipcode"), rf.get("postalCode"), raw.get("zipcode"), raw.get("postalCode")),
    }

    # 1) SCHOOL DISTRICT: if missing, try SchoolDigger; else keep county guess
    if not _ok(d.get("schoolDistrict")):
        city, state = addr_bits.get("city"), addr_bits.get("state")
        sd, src = school_district_from_schooldigger(address=fulladdr, city=city, state=state, lat=lat, lng=lng)
        if _ok(sd):
            d["schoolDistrict"] = sd
            provenance["schoolDistrict"] = src or "schooldigger"
        elif _ok(d.get("schoolDistrictGuess")):
            provenance["schoolDistrict"] = "county_guess"

    # 2) WALK / TRANSIT: if missing (or both None), try Walk Score
    wt = d.get("walkTransit")
    need_wt = not (isinstance(wt, dict) and (_ok(wt.get("walkScore")) or _ok(wt.get("transitScore"))))
    if need_wt and _ok(fulladdr) and (lat is not None and lng is not None):
        scored, src = walk_transit_from_walkscore(address=fulladdr, lat=lat, lng=lng)
        if _ok(scored):
            d["walkTransit"] = scored
            provenance["walkTransit"] = src or "walkscore"

    # 3) RENT: if missing, try RentCast
    zrent = (d.get("rentEstimate") or (d.get("3_valuation_income") or {}).get("rentEstimate"))
    if not _ok(zrent):
        rent, src = rent_from_rentcast(
            address=addr_bits.get("address") or fulladdr,
            city=addr_bits.get("city"),
            state=addr_bits.get("state"),
            zipcode=addr_bits.get("zip"),
        )
        if _ok(rent):
            # Put into top-level details; snapshot builder will also see it
            d["rentEstimate"] = rent
            provenance["rentEstimate"] = src or "rentcast"

    d["provenance"] = provenance
    return d
