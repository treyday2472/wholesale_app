# app/__init__.py
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_apscheduler import APScheduler
from dotenv import load_dotenv
import os

load_dotenv()  # make sure .env is loaded before reading Config

db = SQLAlchemy()
csrf = CSRFProtect()

def _fmt_currency(val):
    try:
        return "${:,.0f}".format(float(val))
    except Exception:
        return val if val not in (None, "") else "—"

def _fmt_percent(val):
    try:
        return "{:.2f}%".format(float(val))
    except Exception:
        return val if val not in (None, "") else "—"

def create_app():
    app = Flask(__name__)
    app.jinja_env.filters["currency"] = _fmt_currency
    app.jinja_env.filters["percent"] = _fmt_percent
    app.config.from_object("config.Config")

    db.init_app(app)
    csrf.init_app(app)

    # Make GOOGLE_MAPS_API_KEY available in ALL templates as {{ GOOGLE_MAPS_API_KEY }}
    @app.context_processor
    def inject_google_key():
        return {"GOOGLE_MAPS_API_KEY": app.config.get("GOOGLE_MAPS_API_KEY", "")}
    from .filters import register_filters
    register_filters(app)
    from .routes import main
    app.register_blueprint(main)

    # Register API blueprint
    from api.routes import api as api_blueprint
    app.register_blueprint(api_blueprint)

        # Start scheduler
    try:
        scheduler = _Scheduler()
        scheduler.init_app(app)
        scheduler.start()
        scheduler.add_job(id='missed_call_autoresponder', func=_job_missed_call_autoresponder, trigger='interval', minutes=5)
        scheduler.add_job(id='nudge_24h', func=_job_24h_nudge, trigger='interval', hours=6)
    except Exception as e:
        app.logger.warning(f'APScheduler not started: {e}')

    with app.app_context():
        db.create_all()

    return app



# ---- APScheduler integration ----
class _Scheduler(APScheduler):
    pass

def _job_missed_call_autoresponder():
    from app import db
    from app.models import LeadEvent, Lead
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    # This is a placeholder logic: find recent 'vonage_voice' events without follow-up
    events = LeadEvent.query.filter(LeadEvent.kind=='vonage_voice', LeadEvent.created_at>=cutoff).all()
    # In real implementation, send SMS through Vonage; for now, just log a LeadEvent
    for evt in events:
        db.session.add(LeadEvent(lead_id=evt.lead_id, kind='auto_sms', payload={'template':'missed_call_followup'}))
    db.session.commit()

def _job_24h_nudge():
    from app import db
    from app.models import Lead, LeadEvent
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(hours=24)
    # Find leads with no events in last 24h and status still 'New Lead'
    stale = Lead.query.filter(Lead.lead_status=='New Lead').all()
    for l in stale:
        # In real implementation, send SMS or email nudge
        db.session.add(LeadEvent(lead_id=l.id, kind='nudge_24h', payload={'note':'nudge queued'}))
    db.session.commit()
