import urllib.parse

def zillow_url(address1=None, city=None, state=None, postalcode=None):
    parts = [p for p in [address1, city, state, postalcode] if p]
    q = ", ".join(parts)
    return "https://www.zillow.com/homes/" + urllib.parse.quote_plus(q)
