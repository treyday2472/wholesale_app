# app/__init__.py
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
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
    app.config.setdefault('API_KEY', os.environ.get('API_KEY'))
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

    from api.routes import api as api_blueprint
    app.register_blueprint(api_blueprint)

    with app.app_context():
        db.create_all()

    return app

