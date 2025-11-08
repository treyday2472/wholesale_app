# app/models/offer.py
from datetime import datetime
from enum import Enum
from .. import db

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
    contact_id = db.Column(db.Integer, db.ForeignKey("contacts.id"), nullable=True)
    property_id = db.Column(db.Integer, db.ForeignKey("properties.id"), nullable=True)

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
