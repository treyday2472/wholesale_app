# app/__init__.py
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
import os

load_dotenv()  # make sure .env is loaded before reading Config

db = SQLAlchemy()
csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    app.config.from_object("config.Config")

    db.init_app(app)
    csrf.init_app(app)

    # Make GOOGLE_MAPS_API_KEY available in ALL templates as {{ GOOGLE_MAPS_API_KEY }}
    @app.context_processor
    def inject_google_key():
        return {"GOOGLE_MAPS_API_KEY": app.config.get("GOOGLE_MAPS_API_KEY", "")}

    from .routes import main
    app.register_blueprint(main)

    with app.app_context():
        db.create_all()

    return app
