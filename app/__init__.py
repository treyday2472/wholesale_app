# app/__init__.py
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate

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
    app.config.from_object("config.Config")

    # Overlay selected env vars (already loaded by run.py)
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
    app.config["SF_ENABLED"] = _as_bool(
        os.getenv("SF_ENABLED", app.config.get("SF_ENABLED", False))
    )

    app.logger.info("Config loaded. ATTOM? %s", bool(app.config.get("ATTOM_API_KEY")))
    app.logger.info("Melissa? %s", bool(app.config.get("MELISSA_API_KEY") or app.config.get("MELISSA_KEY")))

    # Extensions
    db.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)

    # Core Jinja filters
    app.jinja_env.filters["currency"] = _fmt_currency
    app.jinja_env.filters["percent"] = _fmt_percent

    # Optional: zillow_url helper (guarded)
    try:
        from .utils.urls import zillow_url  # optional
        if callable(zillow_url):
            app.jinja_env.filters["zillow_url"] = zillow_url
            # also expose as a callable global for convenience
            @app.context_processor
            def _inject_zillow_url():
                return {"zillow_url": zillow_url}
    except Exception:
        pass

    # Global template vars
    @app.context_processor
    def inject_globals():
        return {
            "GOOGLE_MAPS_API_KEY": app.config.get("GOOGLE_MAPS_API_KEY", ""),
            "SF_ENABLED": app.config.get("SF_ENABLED", False),
            "SF_INSTANCE_URL": app.config.get("SF_INSTANCE_URL", ""),
        }

    # Optional: register any extra filters if you have a module
    try:
        from .filters import register_filters  # noqa: WPS433
        register_filters(app)
    except Exception:
        pass

    # Blueprints (import after extensions)
    from .routes import main as main_bp
    app.register_blueprint(main_bp)

    # Offers blueprint (if present)
    try:
        from .offers.routes import offers_bp  # defines url_prefix="/offers"
        app.register_blueprint(offers_bp)
        app.logger.info("Registered offers blueprint at /offers")
    except Exception:
        pass

    # Voice (optional)
    try:
        from .voicebot import voice as voice_bp  # noqa: WPS433
        app.register_blueprint(voice_bp)
    except Exception:
        pass

    # API blueprint (either app/api or project-level api)
    api_bp = None
    try:
        from .api.routes import api as _api_bp  # app/api/routes.py
        api_bp = _api_bp
    except Exception:
        try:
            from api.routes import api as _api_bp  # api/routes.py at project root
            api_bp = _api_bp
        except Exception:
            api_bp = None

    if api_bp:
        app.register_blueprint(api_bp, url_prefix="/api")
        try:
            csrf.exempt(api_bp)  # JSON endpoints usually don't need CSRF
        except Exception:
            pass

    # Dev convenience
    with app.app_context():
        db.create_all()

    return app
