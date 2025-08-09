import json, os
import requests

def geocode_address(address, google_api_key: str):
    """Use Google Geocoding to normalize -> (lat,lng, formatted_address) or (None,None,address)."""
    if not google_api_key:
        return None, None, address
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": address, "key": google_api_key}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if data.get("results"):
            res = data["results"][0]
            loc = res["geometry"]["location"]
            return loc["lat"], loc["lng"], res.get("formatted_address", address)
        return None, None, address
    except Exception:
        return None, None, address

def fetch_property_facts(address, lat=None, lng=None, rapidapi_key: str = ""):
    """
    Placeholder: hook Zillow/ATTOM/Estated/Melissa/etc.
    Return a dict with any facts we can find. For now, return empty facts.
    """
    # TODO: wire a real data source. For now return defaults.
    return {
        "beds": None,
        "baths": None,
        "sqft": None,
        "lot_size": None,
        "year_built": None,
        "school_district": None,
        "zpid": None,
        "raw": {},
    }

def find_comps_and_arv(lat, lng, beds=None, baths=None, rapidapi_key: str = ""):
    """
    Placeholder comps: here’s where you’d call an API or your own dataset.
    Return comps list + arv estimate (median of sale prices).
    """
    comps = []  # list of dicts: {address, distance_mi, beds, baths, sqft, sold_price, sold_date}
    arv = None
    # TODO: implement real comps logic from your chosen source.
    return comps, arv

def evaluate_property(address, full_address, lat, lng, google_key, rapid_key):
    # Normalize location if needed
    if (lat is None or lng is None) and address:
        lat, lng, full_address = geocode_address(address, google_key)

    facts = fetch_property_facts(full_address or address, lat, lng, rapid_key)
    comps, arv = find_comps_and_arv(lat, lng, facts.get("beds"), facts.get("baths"), rapid_key)

    result = {
        "address": address,
        "full_address": full_address or address,
        "lat": lat, "lng": lng,
        "facts": facts,
        "comps": comps,
        "arv": arv
    }
    return result
