import requests
from flask import current_app

class SalesforceAuthError(Exception): pass
class SalesforceApiError(Exception): pass

def _get_tokens():
    # For first step, we’ll stash tokens in config (dev only).
    # Next step we'll do real OAuth and store in DB.
    access = current_app.config.get("SF_ACCESS_TOKEN")
    instance = current_app.config.get("SF_INSTANCE_URL")
    if not access or not instance:
        raise SalesforceAuthError("No Salesforce token/instance configured yet.")
    return access, instance

def upsert_lead(payload, external_field=None, external_value=None):
    access, instance = _get_tokens()
    ver = current_app.config.get("SF_API_VERSION","v61.0")
    headers = {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}

    if external_field and external_value:
        # Upsert by an external field if you configure one later
        url = f"{instance}/services/data/{ver}/sobjects/Lead/{external_field}/{external_value}"
        r = requests.patch(url, json=payload, headers=headers, timeout=15)
        if r.status_code not in (200, 201, 204):
            raise SalesforceApiError(r.text)
        # On 201, Salesforce returns an Id
        if r.status_code == 201:
            return r.json().get("id")
        # On 204, you should query back if you need the Id
        return None
    else:
        # Simple create
        url = f"{instance}/services/data/{ver}/sobjects/Lead"
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code not in (200, 201):
            raise SalesforceApiError(r.text)
        return r.json().get("id")
