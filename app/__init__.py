# app/__init__.py
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from dotenv import load_dotenv
import os

load_dotenv()

db = SQLAlchemy()
csrf = CSRFProtect()
migrate = Migrate()

def _fmt_currency(val):
    try:
        return "${:,.0f}".format(float(val))
    except Exception:
        return "—" if val in (None, "") else val

def _fmt_percent(val):
    try:
        return "{:.2f}%".format(float(val))
    except Exception:
        return "—" if val in (None, "") else val

def create_app():
    app = Flask(__name__)

    @app.context_processor
    def inject_globals():
        return {
            "GOOGLE_MAPS_API_KEY": app.config.get("GOOGLE_MAPS_API_KEY", ""),
            "SF_ENABLED": app.config.get("SF_ENABLED", False),
            "SF_INSTANCE_URL": app.config.get("SF_INSTANCE_URL", ""),
        }

    # Load config first, then optional env overrides
    app.config.from_object("config.Config")
    if "API_KEY" in os.environ:
        app.config["API_KEY"] = os.environ["API_KEY"]

    app.config["SF_ACCESS_TOKEN"] = os.getenv("SF_ACCESS_TOKEN", "")
    app.config["SF_INSTANCE_URL"] = os.getenv("SF_INSTANCE_URL", "")

    # Init extensions
    db.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)

    # Jinja filters
    app.jinja_env.filters["currency"] = _fmt_currency
    app.jinja_env.filters["percent"] = _fmt_percent

    # Make GOOGLE_MAPS_API_KEY available in all templates
    @app.context_processor
    def inject_google_key():
        return {"GOOGLE_MAPS_API_KEY": app.config.get("GOOGLE_MAPS_API_KEY", "")}

    # Optional: register custom filters
    try:
        from .filters import register_filters
        register_filters(app)
    except Exception:
        pass

    # Blueprints — import AFTER extensions are initialized to avoid circulars
    from .routes import main as main_bp
    app.register_blueprint(main_bp)

    from .voicebot import voice as voice_bp
    app.register_blueprint(voice_bp)

    # Optional API blueprint; prefix it
    try:
        from api.routes import api as api_blueprint
        app.register_blueprint(api_blueprint, url_prefix="/api")
    except Exception:
        pass

    # Dev convenience: create tables if they don't exist
    with app.app_context():
        db.create_all()

    return app
