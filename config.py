# config.py
import os
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev_change_me")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or f"sqlite:///{os.path.join(BASE_DIR, 'app.db')}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

    # Google keys pulled from env
    GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    SECRET_KEY = os.getenv("SECRET_KEY", "dev")
    GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
    RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY", "")
    ZILLOW_HOST = os.getenv("ZILLOW_HOST", "zillow-com1.p.rapidapi.com")

    SF_ENABLED        = os.getenv("SF_ENABLED", "0") == "1"
    SF_AUTH_DOMAIN    = os.getenv("SF_AUTH_DOMAIN", "")  # e.g. login.salesforce.com
    SF_CLIENT_ID      = os.getenv("SF_CLIENT_ID", "")
    SF_CLIENT_SECRET  = os.getenv("SF_CLIENT_SECRET", "")
    SF_REDIRECT_URI   = os.getenv("SF_REDIRECT_URI", "http://127.0.0.1:5000/sf/oauth/callback")
    SF_API_VERSION    = os.getenv("SF_API_VERSION", "v61.0")

    MELISSA_API_KEY = os.environ.get("MELISSA_API_KEY", "")
    MELISSA_KEY     = MELISSA_API_KEY  # alias so both names work

    MELISSA_PROP_URL  = os.environ.get("MELISSA_PROP_URL",
        "https://property.melissadata.net/v4/WEB/LookupProperty/")
    MELISSA_DEEDS_URL = os.environ.get("MELISSA_DEEDS_URL",
        "https://deeds.melissadata.net/v4/WEB/LookupDeeds/")
    MELISSA_HBO_URL   = os.environ.get("MELISSA_HBO_URL",
        "https://property.melissadata.net/v4/WEB/LookupHomesByOwner/")