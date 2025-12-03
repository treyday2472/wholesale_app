# app/services/valuation.py

from ..services.zillow_client import zillow_basics, get_comps_for_zpid

def build_subject_and_comps(prop) -> dict:
    """
    Shared logic for:
      - AI offer context
      - /properties/<id>/comps view

    Returns:
      {
        "subject": { ...zillow_basics... },
        "comps": [ ...normalized comps... ],
        "arv": float | None
      }
    """
    # Build address the same way as in the route
    parts = [
        getattr(prop, "address_line1", "") or "",
        getattr(prop, "city", "") or "",
        getattr(prop, "state", "") or "",
        getattr(prop, "zip", "") or "",
    ]
    full_address = " ".join(p for p in parts if p).strip()

    subject = zillow_basics(full_address)
    zpid = subject.get("zpid")

    comps = get_comps_for_zpid(zpid) if zpid else []

    # Default ARV = subject zestimate
    arv = subject.get("zestimate")

    # If we have comps, compute ARV from them instead
    if comps:
        prices: list[float] = []
        for c in comps:
            p = c.get("sale_price") or c.get("list_price") or c.get("zestimate")
            if p:
                try:
                    prices.append(float(p))
                except Exception:
                    pass

        if prices:
            arv = sum(prices) / len(prices)

    return {
        "subject": subject,
        "comps": comps,
        "arv": arv,
    }
