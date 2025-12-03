# app/offers/routes.py
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify
)

from .. import db
from ..models import Offer, DealType, OfferStatus, Property, Lead

# If you use AI elsewhere you can keep this import;
# it's not used in this file anymore so it's safe either way.
# from ..services.ai import suggest_arv_for_property

offers_bp = Blueprint("offers", __name__, url_prefix="/offers")


def _get_offer(offer_id: int) -> Offer:
    return Offer.query.get_or_404(offer_id)


def _pick_prop_arv(prop: Property | None):
    """
    Safely pick an ARV-like value from the Property.
    Order of preference:
      ai_arv -> arv -> zestimate
    Works even if some attributes don't exist.
    """
    if not prop:
        return None
    for name in ("ai_arv", "arv", "zestimate", "arv_estimate"):
        if hasattr(prop, name):
            v = getattr(prop, name)
            if v not in (None, ""):
                return v
    return None


def _pick_prop_rent(prop: Property | None):
    """
    Safely pick a rent-like value from the Property.
    Order of preference:
      ai_rent_est -> market_rent_est -> rent_zestimate
    """
    if not prop:
        return None
    for name in ("ai_rent_est", "market_rent_est", "rent_zestimate"):
        if hasattr(prop, name):
            v = getattr(prop, name)
            if v not in (None, ""):
                return v
    return None


@offers_bp.get("/")
def offers_root():
    """
    If a property_id or lead_id is provided, jump straight to creating a new offer.
    Otherwise, just send them to properties list.
    """
    pid = request.args.get("property_id", type=int)
    lid = request.args.get("lead_id", type=int)

    if pid or lid:
        return redirect(url_for("offers.new_offer", property_id=pid, lead_id=lid))

    flash(
        "Select a Property or Lead, or pass ?property_id= / ?lead_id= to start an offer.",
        "info",
    )
    return redirect(url_for("main.properties_list"))


@offers_bp.get("/new")
def new_offer():
    property_id = request.args.get("property_id", type=int)
    lead_id = request.args.get("lead_id", type=int)

    # If an offer already exists for this property/lead, open the newest one
    existing = None
    if property_id:
        existing = (
            Offer.query.filter_by(property_id=property_id)
            .order_by(Offer.id.desc())
            .first()
        )
    if not existing and lead_id:
        existing = (
            Offer.query.filter_by(lead_id=lead_id)
            .order_by(Offer.id.desc())
            .first()
        )

    if existing:
        return redirect(url_for("offers.edit_offer", offer_id=existing.id))

    prop = Property.query.get(property_id) if property_id else None
    lead = Lead.query.get(lead_id) if lead_id else None

    # --- ARV & Rent from property (includes AI fields if present) ---
    arv = _pick_prop_arv(prop)
    market_rent_est = _pick_prop_rent(prop)

    offer = Offer(
        property_id=property_id,
        lead_id=lead_id,
        arv=arv,
        market_rent_est=market_rent_est,
    )

    # --- Condition default logic ---
    # 1) If lead/contact has a condition_1_10, use that
    if lead and hasattr(lead, "condition_1_10") and lead.condition_1_10 is not None:
        offer.condition_1_10 = lead.condition_1_10
    # 2) If it's a property only (no lead), default to 7
    elif prop and not lead:
        offer.condition_1_10 = 7

    # --- Mortgage fields from lead/contact ---
    if lead:
        for attr in [
            "has_mortgage",
            "mortgage_balance",
            "mortgage_payment",
            "interest_rate",
            "monthly_taxes",
            "monthly_insurance",
            "reinstatement_amount",
        ]:
            if hasattr(lead, attr):
                setattr(offer, attr, getattr(lead, attr))

    db.session.add(offer)
    db.session.commit()
    return redirect(url_for("offers.edit_offer", offer_id=offer.id))


@offers_bp.get("/<int:offer_id>")
def edit_offer(offer_id):
    offer = _get_offer(offer_id)
    prop = Property.query.get(offer.property_id) if offer.property_id else None
    lead = Lead.query.get(offer.lead_id) if offer.lead_id else None

    # Values for the "Property" side panel on the offer screen
    prop_arv = _pick_prop_arv(prop)
    prop_rent = _pick_prop_rent(prop)

    # Explicit AI values if you persist them on the Property model
    prop_arv_ai = getattr(prop, "ai_arv", None) if prop else None
    prop_rent_ai = getattr(prop, "ai_rent_est", None) if prop else None

    return render_template(
        "offers/edit.html",
        offer=offer,
        prop=prop,
        lead=lead,
        DealType=DealType,
        OfferStatus=OfferStatus,
        # extra context for the read-only Property section
        prop_arv=prop_arv,
        prop_rent=prop_rent,
        prop_arv_ai=prop_arv_ai,
        prop_rent_ai=prop_rent_ai,
    )


@offers_bp.post("/<int:offer_id>")
def save_offer(offer_id):
    offer = _get_offer(offer_id)
    f = request.form

    offer.lead_id = f.get("lead_id") or offer.lead_id
    offer.property_id = f.get("property_id") or offer.property_id
    offer.deal_kind = f.get("deal_kind") or None
    offer.deal_type = f.get("deal_type") or offer.deal_type

    def num(name):
        v = f.get(name, "")
        if v is None:
            return None
        v = v.replace(",", "").replace("$", "").strip()
        return float(v) if v else None

    offer.arv = num("arv")
    offer.market_rent_est = num("market_rent_est")
    offer.has_mortgage = f.get("has_mortgage") == "on"
    offer.mortgage_balance = num("mortgage_balance")
    offer.mortgage_payment = num("mortgage_payment")
    offer.interest_rate = num("interest_rate")
    offer.monthly_taxes = num("monthly_taxes")
    offer.monthly_insurance = num("monthly_insurance")

    cond = f.get("condition_1_10")
    offer.condition_1_10 = int(cond) if cond else None

    offer.reinstatement_amount = num("reinstatement_amount")
    offer.repairs_flip = num("repairs_flip")
    offer.repairs_rental = num("repairs_rental")

    offer.investor_cash_price = num("investor_cash_price")
    offer.end_buyer_price = num("end_buyer_price")
    offer.my_cash_offer = num("my_cash_offer")
    offer.cash_for_equity = num("cash_for_equity")

    # Save AI opinion if present (assuming Offer.ai_opinion exists)
    if hasattr(offer, "ai_opinion"):
        offer.ai_opinion = f.get("ai_opinion") or None

    offer.notes = f.get("notes") or None

    status = f.get("offer_status")
    if status:
        offer.offer_status = status

    db.session.commit()
    flash("Offer saved.", "success")
    return redirect(url_for("offers.edit_offer", offer_id=offer.id))


@offers_bp.post("/<int:offer_id>/status")
def set_status(offer_id):
    offer = _get_offer(offer_id)
    status = request.form.get("status")
    if status in [s.value for s in OfferStatus]:
        offer.offer_status = OfferStatus(status)
        db.session.commit()
        flash(f"Status set to {status}.", "success")
    else:
        flash("Invalid status.", "warning")
    return redirect(url_for("offers.edit_offer", offer_id=offer.id))


@offers_bp.post("/<int:offer_id>/report")
def generate_report(offer_id):
    show_seller = request.form.get("with_seller") == "1"
    offer = _get_offer(offer_id)

    url = f"/static/reports/offer_{offer.id}_{'seller' if show_seller else 'noseller'}.pdf"
    if show_seller:
        offer.report_url_with_seller = url
    else:
        offer.report_url_no_seller = url

    db.session.commit()
    flash("Report generated.", "success")
    return redirect(url_for("offers.edit_offer", offer_id=offer.id))


@offers_bp.post("/<int:offer_id>/offer_letter")
def generate_offer_letter(offer_id):
    offer = _get_offer(offer_id)
    offer.offer_letter_url = f"/static/letters/offer_{offer.id}.pdf"
    db.session.commit()
    flash("Offer letter generated.", "success")
    return redirect(url_for("offers.edit_offer", offer_id=offer.id))


@offers_bp.post("/<int:offer_id>/send_offer_letter")
def send_offer_letter(offer_id):
    offer = _get_offer(offer_id)
    offer.email_sent_at = datetime.utcnow()
    offer.offer_status = OfferStatus.SENT_EMAIL
    db.session.commit()
    flash("Offer letter sent.", "success")
    return redirect(url_for("offers.edit_offer", offer_id=offer.id))


@offers_bp.post("/<int:offer_id>/ai")
def ai_analyze(offer_id):
    """
    Stub AI endpoint. Frontend sends ARV, rent, condition, repairs, offers, etc.
    You can later replace the stubbed 'text' with a real OpenAI call.
    """
    offer = _get_offer(offer_id)
    payload = request.get_json() or {}

    summary = f"""
ARV: {payload.get('arv')}
Rent: {payload.get('market_rent_est')}
Condition (1-10): {payload.get('condition')}
Repairs (flip): {payload.get('repairs_flip')}
Repairs (rental): {payload.get('repairs_rental')}
My cash offer: {payload.get('my_cash_offer')}
Investor cash price: {payload.get('investor_cash_price')}
Mortgage balance: {payload.get('mortgage_balance')}
Mortgage payment: {payload.get('mortgage_payment')}
PITI: {payload.get('piti')}
"""

    text = (
        "Stub AI Opinion:\n\n"
        "Based on the numbers, this looks like a rough starting point. "
        "Replace this with a real OpenAI call to get deal grading, repair commentary, "
        "and suggested offer ranges.\n\n"
        "Inputs:\n" + summary
    )

    if hasattr(offer, "ai_opinion"):
        offer.ai_opinion = text
        db.session.commit()

    return jsonify({"text": text})
