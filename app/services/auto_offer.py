# app/services/auto_offer.py
from __future__ import annotations
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from app.models import db, Lead, Property, Offer, DealType, OfferStatus, LeadEvent
from app.services import enrichers  # <-- the file we built

def _persist_property_from_details(
    lead: Lead, details: Dict[str, Any]
) -> Property:
    """
    Create or update a Property from the enricher details and link it to the Lead.
    """
    prop: Optional[Property] = None
    if lead.property_id:
        prop = Property.query.get(lead.property_id)

    if prop is None:
        prop = Property()
        db.session.add(prop)

    # Map only fields your Property model actually has
    prop.address         = details.get("fullAddress") or lead.address
    prop.full_address    = details.get("fullAddress") or lead.address
    prop.lat             = details.get("lat")
    prop.lng             = details.get("lng")
    prop.zpid            = details.get("zpid")
    prop.source          = "auto_enriched"

    prop.beds            = details.get("beds")
    prop.baths           = details.get("baths")
    prop.sqft            = details.get("sqft")
    prop.lot_size        = details.get("lotSize")
    prop.year_built      = (details.get("yearBuilt") or "") if details.get("yearBuilt") else None
    prop.school_district = details.get("schoolDistrict")

    # Valuation
    prop.arv_estimate    = details.get("arv_estimate")
    # Keep raw blobs if you want them for debugging
    # prop.comps_json    = json.dumps(details.get("comps", [])) if details.get("comps") else None
    # prop.raw_json      = json.dumps({"zillow": details.get("rawZillow"), "melissa": details.get("rawMelissa")})

    db.session.flush()  # assign prop.id
    # Link back to the lead
    lead.property_id = prop.id
    return prop


def _persist_initial_offers(
    lead: Lead, prop: Property, details: Dict[str, Any]
) -> list[Offer]:
    """
    Create Offer rows from details['initial_offers'] (cash + owner-finance baseline).
    Mark them as initial_offer=True so the UI can tag them.
    """
    created: List[Offer] = []
    initial = details.get("initial_offers") or []
    if not initial:
        return created

    for o in initial:
        offer = Offer(
            lead_id=lead.id,
            property_id=prop.id,
            deal_kind=o.get("deal_kind"),
            deal_type=DealType(o.get("deal_type")) if o.get("deal_type") in [x.value for x in DealType] else DealType.CASH,
            arv=o.get("arv"),
            repairs_flip=o.get("repairs_flip"),
            repairs_rental=o.get("repairs_rental"),
            my_cash_offer=o.get("my_cash_offer"),
            end_buyer_price=o.get("end_buyer_price"),
            notes=o.get("notes"),
            offer_status=OfferStatus.MADE,  # seed as 'Offer Made' for initial
            initial_offer=True,
        )
        db.session.add(offer)
        created.append(offer)

    return created


def auto_enrich_and_offer_for_lead(lead_id: int) -> Dict[str, Any]:
    """
    1) Pull fresh details (Zillow → Melissa fallback) using lead's address & condition
    2) Persist Property
    3) Persist baseline Offers (cash + owner-fin)
    4) Record a LeadEvent ("auto_offer_created")
    5) (Optionally) trigger your outbound email/text here
    """
    lead: Lead = Lead.query.get(lead_id)
    if not lead:
        return {"ok": False, "error": "Lead not found"}

    # Try to split city/state/zip if you capture them separately elsewhere; if not, pass None
    city = state = zipcode = None

    details = enrichers.bootstrap_property_from_address(
        address=lead.address or "",
        city=city,
        state=state,
        zipcode=zipcode,
        condition_1_10=lead.condition or "7",
    )

    prop = _persist_property_from_details(lead, details)
    offers = _persist_initial_offers(lead, prop, details)

    # lead status bump if you want to mark that an offer was produced
    if offers:
        lead.lead_status = "3a2: Offer: Sent in Email"  # or keep "New Lead" until you truly email
        db.session.add(LeadEvent(
            lead_id=lead.id,
            kind="auto_offer_created",
            payload={
                "property_id": prop.id,
                "offer_ids": [o.id for o in offers],
                "arv": details.get("arv_estimate"),
                "provenance": details.get("provenance"),
            }
        ))

    db.session.commit()

    return {"ok": True, "lead_id": lead.id, "property_id": prop.id, "offer_ids": [o.id for o in offers]}
