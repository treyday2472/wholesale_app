from typing import Tuple, Dict, Any

REQUIRED_LEAD_FIELDS = ["name", "phone"]
ALLOWED_LEAD_FIELDS = {
    "name", "phone", "email", "source", "notes", "tags",
    "property", "intake"
}

REQUIRED_EVAL_FACTS = ["address"]
ALLOWED_EVAL_FIELDS = {"facts", "comps"}

def validate_lead_payload(payload: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "payload_must_be_object"
    for f in REQUIRED_LEAD_FIELDS:
        if not payload.get(f):
            return False, f"missing_field:{f}"
    # Optional nested property should be a dict if present
    prop = payload.get("property")
    if prop is not None and not isinstance(prop, dict):
        return False, "property_must_be_object"
    # Strip unknowns (optional: enforce whitelist in route)
    return True, ""

def validate_eval_payload(payload: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "payload_must_be_object"
    facts = payload.get("facts") or {}
    if not isinstance(facts, dict):
        return False, "facts_must_be_object"
    for f in REQUIRED_EVAL_FACTS:
        if not facts.get(f):
            return False, f"missing_fact:{f}"
    comps = payload.get("comps") or []
    if not isinstance(comps, list):
        return False, "comps_must_be_array"
    return True, ""
