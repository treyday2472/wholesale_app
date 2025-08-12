from . import db
from datetime import datetime

class Property(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    # address
    address        = db.Column(db.String(200))
    full_address   = db.Column(db.String(300))
    lat            = db.Column(db.Float)
    lng            = db.Column(db.Float)

    # reference ids / external
    zpid           = db.Column(db.String(50))  # optional if you later resolve Zillow
    source         = db.Column(db.String(50), default="manual_or_lead")

    # facts
    beds           = db.Column(db.Float)
    baths          = db.Column(db.Float)
    sqft           = db.Column(db.Integer)
    lot_size       = db.Column(db.Integer)
    year_built     = db.Column(db.String(10))
    school_district = db.Column(db.String(120))

    # valuation
    arv_estimate   = db.Column(db.Integer)     # stored as whole dollars
    comps_json     = db.Column(db.Text)        # JSON string of comps used
    raw_json       = db.Column(db.Text)        # stash raw API responses if you want

    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    seller_first_name = db.Column(db.String(60))
    seller_last_name  = db.Column(db.String(60))
    phone             = db.Column(db.String(20))
    email             = db.Column(db.String(100))
    address           = db.Column(db.String(200))

    # NEW
    occupancy_status  = db.Column(db.String(30))   # vacant / owner_occupied / rented
    closing_date      = db.Column(db.String(20))   # keep string for now (e.g., '2025-08-31')

    condition         = db.Column(db.String(10))   # 1–10 (REQUIRED in form)
    reason            = db.Column(db.Text)
    timeline          = db.Column(db.String(50))
    asking_price      = db.Column(db.String(50))
    property_type     = db.Column(db.String(50))
    lead_source       = db.Column(db.String(100))
    notes             = db.Column(db.Text)

    # Step2 intake (structured answers)
    intake            = db.Column(db.JSON)

    # NEW – optional repair statuses
    ac_status         = db.Column(db.String(30))
    roof_status       = db.Column(db.String(30))
    foundation_status = db.Column(db.String(30))
    water_heater_status = db.Column(db.String(30))
    electrical_status = db.Column(db.String(30))
    plumbing_status   = db.Column(db.String(30))

    image_files       = db.Column(db.Text)
    lead_status       = db.Column(db.String(50), default="New Lead")

class Buyer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name        = db.Column(db.String(60))
    last_name         = db.Column(db.String(60))
    phone             = db.Column(db.String(20))
    email             = db.Column(db.String(100))
    city_focus        = db.Column(db.String(100))     # step 1

    # step 2 criteria
    zip_codes         = db.Column(db.String(300))     # comma-separated
    property_types    = db.Column(db.String(200))     # comma-separated (SFR, Duplex, etc.)
    max_repairs_level = db.Column(db.String(30))      # light / medium / heavy
    max_budget        = db.Column(db.String(50))      # e.g. 300k
    min_beds          = db.Column(db.String(10))
    min_baths         = db.Column(db.String(10))
    notes             = db.Column(db.Text)

    # Step2 intake (structured answers)
    intake            = db.Column(db.JSON)

    source            = db.Column(db.String(100), default="Web Form")

class LeadEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=True)
    kind = db.Column(db.String(50), nullable=False)
    payload = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    lead = db.relationship('Lead', backref=db.backref('events', lazy=True))


def _safe_dict(obj, fields):
    return {f: getattr(obj, f) for f in fields if hasattr(obj, f)}

try:
    def _lead_to_dict(self):
        return _safe_dict(self, ['id','first_name','last_name','phone','email','lead_source','lead_status','created_at','updated_at','notes','property_id'])
    Lead.to_dict = _lead_to_dict
except Exception:
    pass

try:
    def _property_to_dict(self):
        return _safe_dict(self, ['id','address','full_address','city','state','zip_code','beds','baths','sqft','zestimate','rent_zestimate','created_at'])
    Property.to_dict = _property_to_dict
except Exception:
    pass
