import requests

class ZillowError(RuntimeError):
    pass

def _headers(api_key: str, host: str):
    return {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": host,
    }

def autocomplete_address(address: str, api_key: str, host: str):
    """
    Hit Zillow autocomplete to get candidate properties / zpids.
    """
    url = f"https://{host}/autocomplete"
    params = {"q": address}
    r = requests.get(url, headers=_headers(api_key, host), params=params, timeout=20)
    if r.status_code != 200:
        raise ZillowError(f"Autocomplete failed: {r.status_code} {r.text}")
    data = r.json() or {}
    # Typical shape: { "result": [ { "zpid": "...", "address": "...", "city": "...", ... } ] }
    return data.get("result") or data.get("results") or []

def property_details_by_zpid(zpid: str, api_key: str, host: str):
    """
    Optional: fetch richer facts for a zpid.
    """
    url = f"https://{host}/property"
    params = {"zpid": zpid}
    r = requests.get(url, headers=_headers(api_key, host), params=params, timeout=20)
    if r.status_code != 200:
        raise ZillowError(f"Property details failed: {r.status_code} {r.text}")
    return r.json()

def market_data(resource_id: str, api_key: str, host: str, **kwargs):
    """
    Pull market data series. The API accepts various filters; we keep it simple.
    """
    url = f"https://{host}/marketData"
    params = {"resourceId": resource_id}
    params.update(kwargs)  # e.g., {"beds": 3, "propertyTypes": "house"}
    r = requests.get(url, headers=_headers(api_key, host), params=params, timeout=20)
    if r.status_code != 200:
        raise ZillowError(f"marketData failed: {r.status_code} {r.text}")
    return r.json()

def evaluate_address_with_marketdata(address: str, api_key: str, host: str):
    """
    Convenience: address -> autocomplete -> pick best candidate -> marketData.
    Returns dict with { candidate, market, details } when available.
    """
    suggestions = autocomplete_address(address, api_key, host)
    if not suggestions:
        return {"candidate": None, "market": None, "details": None, "note": "No matches from autocomplete."}

    # Pick the first “best” candidate with a zpid/resourceId
    cand = None
    for s in suggestions:
        # different providers return slightly different keys; handle common ones
        if s.get("zpid") or s.get("resourceId") or s.get("id"):
            cand = s
            break
    if not cand:
        return {"candidate": None, "market": None, "details": None, "note": "No candidate with zpid/resourceId."}

    resource_id = cand.get("resourceId") or cand.get("zpid") or cand.get("id")
    mkt = market_data(str(resource_id), api_key, host)

    details = None
    if cand.get("zpid"):
        try:
            details = property_details_by_zpid(str(cand["zpid"]), api_key, host)
        except Exception:
            details = None

    return {"candidate": cand, "market": mkt, "details": details}
