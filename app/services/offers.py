# app/services/offers.py
from datetime import datetime
from typing import Optional
from .. import db
from ..models import Offer, Lead, DealType, OfferStatus, Property

def _get_arv_for_lead(lead: Lead) -> Optional[float]:
    # Prefer linked property ARV
    if lead.property and lead.property.arv_estimate:
        return float(lead.property.arv_estimate)
    # Fallback: intake payload may carry 'arv'
    try:
        if lead.intake and lead.intake.get("arv"):
            return float(lead.intake["arv"])
    except Exception:
        pass
    return None

def ensure_initial_offer(lead: Lead) -> Optional[Offer]:
    """Idempotent: create initial auto offer if it doesn't exist and inputs are present."""
    # Already have one?
    existing = (
        Offer.query.filter_by(lead_id=lead.id, initial_offer=True)
        .order_by(Offer.created_at.desc())
        .first()
    )
    if existing:
        return existing

    # Need a way to contact + an ARV
    if not (lead.email or lead.phone):
        return None

    arv = _get_arv_for_lead(lead)
    if not arv or arv <= 0:
        return None

    # condition default 7
    try:
        cond = int(str(lead.condition or "7"))
    except Exception:
        cond = 7
    cond = min(max(cond, 1), 10)

    # Formula
    repairs = (10 - cond) * 0.045 * arv
    my_cash_offer = arv - repairs

    offer = Offer(
        lead_id=lead.id,
        property_id=lead.property_id,
        deal_kind="Auto",
        deal_type=DealType.CASH,
        arv=arv,
        condition_1_10=cond,
        repairs_flip=repairs,
        repairs_rental=repairs,  # start equal; you can split later
        my_cash_offer=my_cash_offer,
        offer_status=OfferStatus.MADE,
        initial_offer=True,
        notes="Auto-generated from lead save.",
    )
    db.session.add(offer)
    db.session.commit()
    return offer
