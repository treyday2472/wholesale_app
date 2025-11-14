from . import db
from datetime import datetime
from enum import Enum


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
    property_id = db.Column(db.Integer, db.ForeignKey("property.id"), nullable=True)
    property = db.relationship("Property", backref=db.backref("leads", lazy=True))

    sf_lead_id = db.Column(db.String(32), nullable=True)

    seller_first_name = db.Column(db.String(60))
    seller_last_name  = db.Column(db.String(60))
    phone             = db.Column(db.String(20))
    email             = db.Column(db.String(100))
    address           = db.Column(db.String(200))

    # NEW
    occupancy_status  = db.Column(db.String(30))   # vacant / owner_occupied / rented
    closing_date      = db.Column(db.String(20))   # keep string for now (e.g., '2025-08-31')
    occupancy_status    = db.Column(db.String(20))
    listed_with_realtor = db.Column(db.String(5))    # "yes"/"no" or Boolean
    property_type       = db.Column(db.String(50))
    condition         = db.Column(db.String(10), default="7")
    why_sell            = db.Column(db.Text)         # if you want it as a column
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

    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at        = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)



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

class DealType(str, Enum):
    CASH = "Cash"
    OWNER_FINANCE = "Owner Finance"
    LEASE = "Lease"
    LEASE_OPTION = "Lease Option"
    SUBJECT_TO = "Subject To"
    NEW_CONSTRUCTION = "New Construction"

class OfferStatus(str, Enum):
    MADE = "Offer Made"
    REJECTED = "Rejected"
    ACCEPTED = "Accepted"
    SOLD = "Sold"
    SENT_EMAIL = "Offer Sent in Email"
    THINKING = "Thinking about it"
    NO_RESPONSE = "No response"

class Offer(db.Model):
    __tablename__ = "offers"
    id = db.Column(db.Integer, primary_key=True)

    # relationships
    lead_id = db.Column(db.Integer, db.ForeignKey("lead.id"), nullable=True)
    lead    = db.relationship("Lead", backref=db.backref("offers", lazy=True))

    property_id = db.Column(db.Integer, db.ForeignKey("property.id"), nullable=True)  # <-- change here
    prop        = db.relationship("Property", backref=db.backref("offers", lazy=True))

    # high-level categorization
    deal_kind = db.Column(db.String(64))   # e.g., "Flip" or "Rental" (your terms)
    deal_type = db.Column(db.Enum(DealType), default=DealType.CASH, nullable=False)

    # pulled/derived numbers
    arv = db.Column(db.Float)                    # from Property details page
    market_rent_est = db.Column(db.Float)

    # mortgage / holding costs
    has_mortgage = db.Column(db.Boolean, default=False)
    mortgage_balance = db.Column(db.Float)
    mortgage_payment = db.Column(db.Float)
    interest_rate = db.Column(db.Float)          # % annual (e.g., 6.5 means 6.5%)
    monthly_taxes = db.Column(db.Float)
    monthly_insurance = db.Column(db.Float)

    # condition-based inputs
    condition_1_10 = db.Column(db.Integer)       # seller-stated condition, 1–10
    reinstatement_amount = db.Column(db.Float)

    # repairs (store overrides, we’ll still compute live in the UI)
    repairs_flip = db.Column(db.Float)
    repairs_rental = db.Column(db.Float)

    # money fields
    investor_cash_price = db.Column(db.Float)    # “Investor cash price”
    end_buyer_price = db.Column(db.Float)        # “End buyer sale price”
    my_cash_offer = db.Column(db.Float)          # “My cash offer I'm offering”
    cash_for_equity = db.Column(db.Float)

    # meta
    notes = db.Column(db.Text)
    offer_status = db.Column(db.Enum(OfferStatus))
    initial_offer = db.Column(db.Boolean, default=False)  # 👈 mark “auto” offer
    offer_letter_url = db.Column(db.String(512))
    report_url_with_seller = db.Column(db.String(512))
    report_url_no_seller = db.Column(db.String(512))
    email_sent_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def piti(self):
        """Monthly PITI if given: mortgage_payment + taxes + insurance."""
        base = (self.mortgage_payment or 0.0)
        return base + (self.monthly_taxes or 0.0) + (self.monthly_insurance or 0.0)
    
# app/models.py (add/adjust)
import enum
from . import db

class LeadStatus(enum.Enum):
    NEW_LEAD = "New Lead"
    # Pipeline (from your Podio list)
    CONTACTED_FOLLOWUP   = "1. Contacted: After Call Follow up"
    CONTACTED_NOT_READY  = "1b: Contacted: Not ready To Sell"
    INSP_SCHEDULED       = "2: Inspection: Scheduled"
    INSP_NO_SHOW         = "2b. Inspection: No Show"
    INSP_COMPLETED       = "2c. Inspection: Completed"
    OFFER_VERBAL         = "3a1: Offer: Made Verbally"
    OFFER_SENT_EMAIL     = "3a2: Offer: Sent in Email"
    OFFER_THINKING       = "3b: Offer: Thinking about it"
    OFFER_NO_RESPONSE    = "3b2: Offer: No response"
    OFFER_REJECTED       = "3c1: Offer: Rejected"
    OFFER_ACCEPTED       = "3d: Offer: Accepted"
    CONTRACT_SENT        = "4: Contract: Sent"
    CONTRACT_EXPIRED     = "4b: Contract: Expired"
    TITLE_UNDER_CONTRACT = "5: Title: Under Contract"
    TITLE_NOT_CLEARED    = "5b: Title NOT Cleared"
    TITLE_CLEARED        = "5c: Title Cleared"
    MARKETING            = "6: Marketing"
    SHOWING              = "7: Showing"
    ASSIGNMENT_SENT      = "8: Assignment: Sent"
    ASSIGNMENT_SIGNED    = "8a: Assignment Signed"
    ASSIGNED_EARNEST     = "8b: Assigned & Earnest Collected"
    CLOSED               = "9. Closed"
    # Exit buckets
    X_UNREALISTIC        = "x1: Unrealistic Seller"
    X_NOT_INTERESTED     = "x2: Not Interested"
    X_REFERRED_REALTOR   = "x3: Referred to Realtor"
    X_UNDER_CONTRACT_ELSE= "x4: Under contract with someone else"
    X_WRONG_NUMBER       = "x5: wrong number"
    X_FOLLOW_UP_SCHED    = "x7: follow up scheduled"
    X_NO_DEAL            = "x8: No deal"
    X_ALREADY_SOLD       = "x9: already sold"
    X_OLD_IMPORT         = "x10: Old Lead just imported"
    X_DO_NOT_CONTACT     = "x11 DO NOT contact"

LEAD_STATUS_ORDER = [s.value for s in LeadStatus]
