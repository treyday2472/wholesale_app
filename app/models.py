from . import db

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

    # NEW – optional repair statuses
    ac_status         = db.Column(db.String(30))
    roof_status       = db.Column(db.String(30))
    foundation_status = db.Column(db.String(30))
    water_heater_status = db.Column(db.String(30))
    electrical_status = db.Column(db.String(30))
    plumbing_status   = db.Column(db.String(30))

    image_files       = db.Column(db.Text)
    lead_status       = db.Column(db.String(50), default="New Lead")
