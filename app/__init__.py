# app/__init__.py
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from dotenv import load_dotenv, find_dotenv

# Load .env from project root no matter the CWD
load_dotenv(find_dotenv(), override=True)

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


def _as_bool(value, default=False):
    if value is None:
        return default
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return default


def create_app():
    app = Flask(__name__)

    # Base config object (e.g., SECRET_KEY, SQLALCHEMY_DATABASE_URI, etc.)
    app.config.from_object("config.Config")

    # Overlay selected environment variables into app.config
    # (ATTOM key is the important one here)
    overlays = {
        "API_KEY": os.getenv("API_KEY", app.config.get("API_KEY", "")),
        "ATTOM_API_KEY": os.getenv("ATTOM_API_KEY", app.config.get("ATTOM_API_KEY", "")),
        "RAPIDAPI_KEY": os.getenv("RAPIDAPI_KEY", app.config.get("RAPIDAPI_KEY", "")),
        "ZILLOW_HOST": os.getenv("ZILLOW_HOST", app.config.get("ZILLOW_HOST", "")),
        "PROPERTY_HOST": os.getenv("PROPERTY_HOST", app.config.get("PROPERTY_HOST", "")),
        "MELISSA_API_KEY": os.getenv("MELISSA_API_KEY", app.config.get("MELISSA_API_KEY", "")),
        "MELISSA_KEY": os.getenv("MELISSA_KEY", app.config.get("MELISSA_KEY", "")),
        "GOOGLE_MAPS_API_KEY": os.getenv("GOOGLE_MAPS_API_KEY", app.config.get("GOOGLE_MAPS_API_KEY", "")),
        "SF_ACCESS_TOKEN": os.getenv("SF_ACCESS_TOKEN", app.config.get("SF_ACCESS_TOKEN", "")),
        "SF_INSTANCE_URL": os.getenv("SF_INSTANCE_URL", app.config.get("SF_INSTANCE_URL", "")),
    }
    app.config.update(overlays)
    # Booleans (parse explicitly)
    app.config["SF_ENABLED"] = _as_bool(os.getenv("SF_ENABLED", app.config.get("SF_ENABLED", False)))

    # Quick visibility in logs (won’t print sensitive values)
    app.logger.info("Config loaded. ATTOM key present? %s", bool(app.config.get("ATTOM_API_KEY")))
    app.logger.info("Melissa key present? %s", bool(app.config.get("MELISSA_API_KEY") or app.config.get("MELISSA_KEY")))

    # Init extensions
    db.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)

    # Jinja filters
    app.jinja_env.filters["currency"] = _fmt_currency
    app.jinja_env.filters["percent"] = _fmt_percent

    # Make a few config values available globally in templates
    @app.context_processor
    def inject_globals():
        return {
            "GOOGLE_MAPS_API_KEY": app.config.get("GOOGLE_MAPS_API_KEY", ""),
            "SF_ENABLED": app.config.get("SF_ENABLED", False),
            "SF_INSTANCE_URL": app.config.get("SF_INSTANCE_URL", ""),
        }

    # Optional: register custom filters (if present)
    try:
        from .filters import register_filters
        register_filters(app)
    except Exception:
        pass

    # Blueprints — import AFTER extensions initialized to avoid circular imports
    from .routes import main as main_bp
    app.register_blueprint(main_bp)

    try:
        from .voicebot import voice as voice_bp
        app.register_blueprint(voice_bp)
    except Exception:
        pass

    # Optional API blueprint
    try:
        from api.routes import api as api_blueprint
        app.register_blueprint(api_blueprint, url_prefix="/api")
    except Exception:
        pass

    # Dev convenience: ensure tables exist
    with app.app_context():
        db.create_all()

    return app
