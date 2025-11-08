# app/utils/urls.py
from urllib.parse import quote_plus

def zillow_url(zpid=None, address=None):
    """
    Build a reasonable Zillow URL.
    - Prefer zpid when present, else fall back to an address search.
    """
    if zpid:
        # zpid detail pattern
        return f"https://www.zillow.com/homedetails/{zpid}_zpid/"
    if address:
        return f"https://www.zillow.com/homes/{quote_plus(address)}/"
    return "https://www.zillow.com/"
