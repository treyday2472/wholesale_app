# app/services/zillow_fetch.py
import os, math, time, logging, requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional

RAPIDAPI_HOST = os.getenv("ZILLOW_RAPIDAPI_HOST", "zillow56.p.rapidapi.com")
RAPIDAPI_KEY  = os.getenv("ZILLOW_RAPIDAPI_KEY")

def _miles(lat1, lon1, lat2, lon2) -> Optional[float]:
    try:
        # quick haversine
        from math import radians, sin, cos, sqrt, atan2
        R = 3958.8
        dlat = radians(lat2-lat1); dlon = radians(lon2-lon1)
        a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
        return 2*R*atan2(sqrt(a), sqrt(1-a))
    except Exception:
        return None

def _clean_address(item: dict) -> str:
    # Try common Zillow fields; be defensive
    a = item.get("address") or {}
    parts = [
        a.get("streetAddress") or item.get("streetAddress"),
        a.get("city") or item.get("city"),
        a.get("state") or item.get("state"),
        a.get("zipcode") or a.get("zipcode") or item.get("zipcode")
    ]
    return ", ".join([p for p in parts if p])

def _parse_price(v):
    try:
        return float(v)
    except Exception:
        return None

def _parse_date(dstr: str) -> Optional[str]:
    # Normalize to YYYY-MM-DD if possible
    if not dstr:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%m/%d/%Y"):
        try:
            return datetime.strptime(dstr[:19], fmt).date().isoformat()
        except Exception:
            continue
    # Sometimes we only get epoch:
    try:
        ts = int(dstr)
        return datetime.utcfromtimestamp(ts).date().isoformat()
    except Exception:
        return None

def _headers():
    if not RAPIDAPI_KEY:
        raise RuntimeError("Missing ZILLOW_RAPIDAPI_KEY")
    return {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}

def search_recently_sold(address: str, lat: float, lng: float,
                          radius_miles: float = 0.5,
                          months_back: int = 6,
                          max_results: int = 40) -> List[Dict]:
    """
    Use RapidAPI's Zillow search to pull 'recentlySold' results near a point.
    We'll do a radius-by-bounds approximation around (lat,lng).
    """
    if not RAPIDAPI_KEY:
        logging.warning("Zillow search skipped: no RAPIDAPI key.")
        return []

    # build a small bounding box around the subject
    # ~69 miles per degree lat; adjust lon by cos(lat)
    lat_delta = radius_miles / 69.0
    lon_delta = radius_miles / (69.0 * max(0.1, math.cos(math.radians(lat))))

    west  = lng - lon_delta
    east  = lng + lon_delta
    south = lat - lat_delta
    north = lat + lat_delta

    url = f"https://{RAPIDAPI_HOST}/search"
    params = {
        "status_type": "RecentlySold",
        "resultsPerPage": min(50, max_results),
        # Zillow56 supports either location string or bounds; we’ll pass both
        "location": address,
        "mapBounds": f"{north},{west},{south},{east}"
    }

    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=12)
        r.raise_for_status()
        data = r.json() or {}
        items = data.get("results") or data.get("props") or []
    except Exception as e:
        logging.warning("Zillow search failed: %s", e)
        return []

    out: List[Dict] = []
    cutoff = datetime.utcnow().date() - timedelta(days=months_back*30)

    for it in items:
        # Try to normalize common fields
        lat2 = it.get("latitude") or (it.get("latLong") or {}).get("latitude")
        lng2 = it.get("longitude") or (it.get("latLong") or {}).get("longitude")
        if lat2 is None or lng2 is None:
            continue

        dist = _miles(lat, lng, float(lat2), float(lng2))

        price = (
            _parse_price(it.get("price")) or
            _parse_price(it.get("unformattedPrice")) or
            _parse_price(it.get("lastSoldPrice"))
        )
        sqft  = it.get("livingArea") or it.get("livingAreaValue") or it.get("sqft")
        beds  = it.get("bedrooms") or it.get("beds")
        baths = it.get("bathrooms") or it.get("baths")
        yr    = it.get("yearBuilt")
        sold  = _parse_date(it.get("dateSold") or it.get("soldDate"))
        # sold filter (recent)
        if sold:
            try:
                if datetime.fromisoformat(sold).date() < cutoff:
                    continue
            except Exception:
                pass

        urlz = it.get("hdpUrl") or it.get("detailUrl") or it.get("zillowUrl")
        addr = _clean_address(it)

        comp = {
            "address": addr,
            "_addr_line": addr,
            "saleDate": sold,
            "price": price,
            "beds": beds,
            "baths": baths,
            "sqft": sqft,
            "yearBuilt": yr,
            "distance": dist,
            "zillow_url": f"https://www.zillow.com{urlz}" if urlz and urlz.startswith("/") else urlz,
            "propclass": it.get("homeType") or it.get("homeTypeV2") or it.get("homeTypeDisplay")
        }

        # Keep only closed sales with a price
        if price and sold:
            out.append(comp)

    # Sort by recency + distance
    out.sort(key=lambda c: ((c.get("saleDate") or "0000-00-00"), c.get("distance") or 99), reverse=True)
    return out[:max_results]
