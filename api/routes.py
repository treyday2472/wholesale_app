from flask import jsonify, request, current_app
from . import api
from .auth import require_token
from app import db
from app.models import Lead, Property, LeadEvent
from datetime import datetime

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
    # Minimal create: accept name/phone/email and property basics
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

    # Optional property attach
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

    # Log event
    evt = LeadEvent(
        lead_id=l.id,
        kind='api_create',
        payload_json=data
    )
    db.session.add(evt)
    db.session.commit()

    resp = l.to_dict()
    if prop:
        resp['property'] = prop.to_dict()
    return jsonify(resp), 201

# --- Webhooks ---

@api.route('/webhooks/vonage/sms', methods=['POST'])
def vonage_sms():
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    # Extract phone if available to attach to an existing lead
    phone = payload.get('msisdn') or payload.get('from') or payload.get('sender')
    lead = None
    if phone:
        lead = Lead.query.filter(Lead.phone == phone).order_by(Lead.created_at.desc()).first()

    evt = LeadEvent(
        lead_id=lead.id if lead else None,
        kind='vonage_sms',
        payload_json=payload
    )
    db.session.add(evt)
    db.session.commit()
    return jsonify(received=True)

@api.route('/webhooks/vonage/voice', methods=['POST'])
def vonage_voice():
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    phone = payload.get('from') or payload.get('caller') or payload.get('msisdn')
    lead = None
    if phone:
        lead = Lead.query.filter(Lead.phone == phone).order_by(Lead.created_at.desc()).first()
    evt = LeadEvent(
        lead_id=lead.id if lead else None,
        kind='vonage_voice',
        payload_json=payload
    )
    db.session.add(evt)
    db.session.commit()
    return jsonify(received=True)

@api.route('/webhooks/google-ads', methods=['POST'])
def google_ads_webhook():
    require_token()
    payload = request.get_json(silent=True) or {}
    evt = LeadEvent(kind='google_ads', payload_json=payload)
    db.session.add(evt)
    db.session.commit()
    return jsonify(status='ok')

@api.route('/webhooks/facebook-leads', methods=['POST'])
def facebook_leads_webhook():
    require_token()
    payload = request.get_json(silent=True) or {}
    evt = LeadEvent(kind='facebook_leads', payload_json=payload)
    db.session.add(evt)
    db.session.commit()
    return jsonify(status='ok')
