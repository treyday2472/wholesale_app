# app/offers/routes.py
from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, jsonify
)
from .. import db
from ..models import Offer, DealType, OfferStatus, Property, Lead

offers_bp = Blueprint("offers", __name__, url_prefix="/offers")


def _get_offer(offer_id):
    return Offer.query.get_or_404(offer_id)

# app/offers/routes.py

@offers_bp.get("/")
def offers_root():
    """
    If a property_id or lead_id is provided, jump straight to creating a new offer.
    Otherwise, nudge the user and send them somewhere useful.
    """
    pid = request.args.get("property_id", type=int)
    lid = request.args.get("lead_id", type=int)
    if pid or lid:
        return redirect(url_for("offers.new_offer", property_id=pid, lead_id=lid))
    flash("Select a Property or Lead, or pass ?property_id= / ?lead_id= to start an offer.", "info")
    return redirect(url_for("main.properties_list"))


@offers_bp.get("/new")
def new_offer():
    property_id = request.args.get("property_id", type=int)
    lead_id = request.args.get("lead_id", type=int)

    # If an offer already exists for this property/lead, open the newest one instead of creating duplicates
    existing = None
    if property_id:
        existing = Offer.query.filter_by(property_id=property_id).order_by(Offer.id.desc()).first()
    if not existing and lead_id:
        existing = Offer.query.filter_by(lead_id=lead_id).order_by(Offer.id.desc()).first()
    if existing:
        return redirect(url_for("offers.edit_offer", offer_id=existing.id))

    prop = Property.query.get(property_id) if property_id else None
    lead = Lead.query.get(lead_id) if lead_id else None

    offer = Offer(
        property_id=property_id,
        lead_id=lead_id,
        arv=(prop.zestimate if prop and getattr(prop, "zestimate", None) else None),
        market_rent_est=(getattr(prop, "rent_zestimate", None) or None),
    )
    db.session.add(offer)
    db.session.commit()
    return redirect(url_for("offers.edit_offer", offer_id=offer.id))

@offers_bp.get("/<int:offer_id>")
def edit_offer(offer_id):
    offer = _get_offer(offer_id)
    prop = Property.query.get(offer.property_id) if offer.property_id else None
    lead = Lead.query.get(offer.lead_id) if offer.lead_id else None
    return render_template(
        "offers/edit.html",
        offer=offer, prop=prop, lead=lead,
        DealType=DealType, OfferStatus=OfferStatus
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
        v = f.get(name, "").replace(",", "").strip()
        return float(v) if v not in ("", None) else None

    offer.arv = num("arv")
    offer.market_rent_est = num("market_rent_est")
    offer.has_mortgage = (f.get("has_mortgage") == "on")
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
