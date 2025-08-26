
from flask import jsonify, request, current_app
from . import api
from .auth import require_token

from .. import db
from ..models import Lead, Property, LeadEvent
from datetime import datetime
from ..services import attom as attom_svc


@api.route('/health', methods=['GET'])
def health():
    return jsonify(status='ok')

@api.route('/leads', methods=['GET'])
def list_leads():
    q = Lead.query.order_by(Lead.created_at.desc()).limit(200).all()
    return jsonify([l.to_dict() for l in q])

@api.route('/leads/<int:lead_id>', methods=['GET'])
def get_lead(lead_id):
    l = Lead.query.get_or_404(lead_id)
    return jsonify(l.to_dict())

@api.route('/leads', methods=['POST'])
def create_lead():
    require_token()
    data = request.get_json(silent=True) or {}
    l = Lead(
        first_name=data.get('first_name') or data.get('name'),
        last_name=data.get('last_name'),
        phone=data.get('phone'),
        email=data.get('email'),
        lead_source=data.get('source') or data.get('lead_source') or 'API',
        lead_status=data.get('status') or 'New Lead',
        notes=data.get('notes')
    )
    db.session.add(l)
    db.session.flush()

    prop = None
    prop_data = data.get('property') or {}
    if prop_data:
        prop = Property(
            address=prop_data.get('address'),
            full_address=prop_data.get('full_address'),
            city=prop_data.get('city'),
            state=prop_data.get('state'),
            zip_code=prop_data.get('zip'),
            beds=prop_data.get('beds'),
            baths=prop_data.get('baths'),
            sqft=prop_data.get('sqft'),
            source='API'
        )
        db.session.add(prop)
        db.session.flush()
        l.property_id = prop.id

    evt = LeadEvent(lead_id=l.id, kind='api_create', payload={**data})
    db.session.add(evt)
    db.session.commit()

    resp = l.to_dict()
    if prop:
        resp['property'] = prop.to_dict()
    return jsonify(resp), 201

@api.route('/webhooks/vonage/sms', methods=['POST'])
def vonage_sms():
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    phone = payload.get('msisdn') or payload.get('from') or payload.get('sender')
    lead = Lead.query.filter(Lead.phone == phone).order_by(Lead.created_at.desc()).first() if phone else None
    evt = LeadEvent(lead_id=lead.id if lead else None, kind='vonage_sms', payload=payload)
    db.session.add(evt)
    db.session.commit()
    return jsonify(received=True)

@api.route('/webhooks/vonage/voice', methods=['POST'])
def vonage_voice():
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    phone = payload.get('from') or payload.get('caller') or payload.get('msisdn')
    lead = Lead.query.filter(Lead.phone == phone).order_by(Lead.created_at.desc()).first() if phone else None
    evt = LeadEvent(lead_id=lead.id if lead else None, kind='vonage_voice', payload=payload)
    db.session.add(evt)
    db.session.commit()
    return jsonify(received=True)

@api.route('/webhooks/google-ads', methods=['POST'])
def google_ads_webhook():
    require_token()
    payload = request.get_json(silent=True) or {}
    evt = LeadEvent(kind='google_ads', payload=payload)
    db.session.add(evt)
    db.session.commit()
    return jsonify(status='ok')

@api.route('/webhooks/facebook-leads', methods=['POST'])
def facebook_leads_webhook():
    require_token()
    payload = request.get_json(silent=True) or {}
    evt = LeadEvent(kind='facebook_leads', payload=payload)
    db.session.add(evt)
    db.session.commit()
    return jsonify(status='ok')

@api.route('/evaluate', methods=['POST'])
def evaluate():
    from ..app.services.property_eval import evaluate_property, evaluate_exit_strategies
    data = request.get_json(silent=True) or {}

    facts = data.get('facts') or {}
    comps = data.get('comps') or []

    try:
        base = evaluate_property(facts, comps)
        base_payload = {
            "arv": base.arv,
            "repairs": base.repairs,
            "mao_cash": base.mao_cash,
            "wholetail_offer": base.wholetail_offer,
            "rental_summary": base.rental_summary,
            "notes": base.notes
        }
    except Exception as e:
        current_app.logger.warning(f"evaluate_property error: {e}")
        base_payload = {"error": "base_evaluation_failed"}

    try:
        mapped = {
            "arv": facts.get("arv") or facts.get("ARV") or facts.get("arv_manual") or facts.get("zestimate") or base_payload.get("arv"),
            "repairs_flip": facts.get("repairs") or facts.get("repairs_flip") or facts.get("Real Repairs (flip)") or base_payload.get("repairs"),
            "repairs_rental_of": facts.get("repairs_rental_of") or 0,
            "investor_cash_price": facts.get("investor_cash_price"),
            "cash_offer": facts.get("cash_offer"),
            "monthly_taxes": facts.get("monthly_taxes"),
            "insurance": facts.get("insurance"),
            "reinstatement_amount": facts.get("reinstatement_amount") or 0,
            "cash_for_equity": facts.get("cash_for_equity") or 0,
            "market_rent": facts.get("market_rent") or facts.get("rent") or facts.get("rent_zestimate"),
            "assumptions": facts.get("assumptions"),
            "acres": facts.get("acres"),
            "ppa": facts.get("ppa") or facts.get("baseline_ppa"),
            "utilities": facts.get("utilities"),
        }
        exit_strats = evaluate_exit_strategies(mapped)
    except Exception as e:
        current_app.logger.warning(f"evaluate_exit_strategies error: {e}")
        exit_strats = {"error": "exit_strategies_failed"}

    return jsonify({"base": base_payload, "exits": exit_strats})
