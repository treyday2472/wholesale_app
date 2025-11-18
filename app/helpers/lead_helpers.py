# app/helpers/lead_helpers.py
from ..models import Lead, Property, Offer
from .. import db

def ensure_property_and_initial_offer(lead: Lead):
    # Property
    if lead.property:
        prop = lead.property
    else:
        prop = Property(address=lead.address)
        prop.lead = lead  # links both ways

    # Initial offer
    existing = next((o for o in lead.offers if o.initial_offer), None)
    if existing:
        offer = existing
    else:
        offer = Offer(lead=lead, prop=prop, initial_offer=True)
        db.session.add(offer)

    return offer
