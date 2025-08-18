from typing import Optional, Dict, Any
from .zillow_client import investor_snapshot_by_address, investor_snapshot_by_zpid, ZillowError

def build_snapshot_for_property(prop) -> Optional[Dict[str, Any]]:
    """
    Given your SQLAlchemy Property row (with fields like full_address, zpid),
    return the investor snapshot dict or None if lookup fails.
    """
    try:
        if getattr(prop, "zpid", None):
            return investor_snapshot_by_zpid(str(prop.zpid), include_market=True)
        # fallback to address
        addr = getattr(prop, "full_address", None) or getattr(prop, "address", None)
        if addr:
            return investor_snapshot_by_address(addr, include_market=True)
    except ZillowError:
        pass
    return None
