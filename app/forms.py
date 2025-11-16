from flask_wtf import FlaskForm
from wtforms import (
    StringField, TextAreaField, SelectField, SubmitField, HiddenField, IntegerField, DecimalField
)
from wtforms.validators import DataRequired, Optional, Email, Length
from wtforms.fields import MultipleFileField


# Optional helper choices
REPAIR_LEVEL_CHOICES = [
    ("", "— Select —"),
    ("cosmetics", "Cosmetics"),
    ("light", "Light repairs"),
    ("heavy", "Heavy repairs"),
    ("complete_rehab", "Complete rehab"),
]

PROPERTY_TYPE_CHOICES = [
    ("", "— Select —"),
    ("single_family", "Single Family Home"),
    ("duplex", "Duplex"),
    ("condo", "Condo/Townhome"),
    ("land", "Land"),
    ("mobile", "Mobile/Manufactured Home"),
    ("other", "Other"),
]

TIMELINE_CHOICES = [
    ("", "— Select —"),
    ("30_days", "30 Days"),
    ("60_days", "60 Days"),
    ("90_days", "90 Days"),
    ("6_months", "6 Months"),
    ("12_months", "12 Months"),


]

OCCUPANCY_CHOICES = [
    ("", "— Select —"),
    ("owner_occupied", "Owner occupied"),
    ("rented", "Rented out"),
    ("vacant", "Vacant")
]

WORTH_CHOICES = [
    ("", "— Select —"),
    ("lt100", "Less than $100k"),
    ("100-200", "$100k – $200k"),
    ("200-300", "$200k – $300k"),
    ("300-400", "$300k – $400k"),
    ("400-500", "$400k – $500k"),
    ("500-600", "$500k – $600k"),
    ("600-700", "$600k – $700k"),
    ("gt700", "More than $700k"),
]

class PropertyForm(FlaskForm):
    """Used for quick property lookups/evaluation."""
    address      = StringField("Property Address", validators=[DataRequired()])
    full_address = HiddenField()
    lat          = HiddenField()
    lng          = HiddenField()
    submit       = SubmitField("Save & Evaluate")


class LeadStep1Form(FlaskForm):
    """Step 1: basic seller + property contact info."""
    seller_first_name = StringField("First Name", validators=[DataRequired()])
    seller_last_name  = StringField("Last Name", validators=[Optional()])
    email             = StringField("Email", validators=[Email(), DataRequired()])
    phone             = StringField("Phone Number", validators=[DataRequired()])

    address      = StringField("Property Address", validators=[DataRequired()])
    full_address = HiddenField()
    lat          = HiddenField()
    lng          = HiddenField()
    lead_source = "Web Form"


    submit = SubmitField("Continue")



# --- Step 2 (CORE ONLY) ---
class LeadStep2CoreForm(FlaskForm):
    why_sell = TextAreaField("1) Why do you want to sell?", validators=[Optional(), Length(max=500)], )

    timeline = SelectField(
        "2) How soon do you want to sell your property?",
        choices=TIMELINE_CHOICES, validators=[DataRequired(message="Please choose a property type.")],
    )

    property_type = SelectField(
        "2) What type of property is it?",
        choices=PROPERTY_TYPE_CHOICES, validators=[DataRequired(message="Please choose a property type.")],
    )

    occupancy_status = SelectField(
        "3) Is it owner occupied, rented out, or vacant?",
        choices= OCCUPANCY_CHOICES,
        validators=[DataRequired(message="Please choose an occupancy status.")],
    )

    rent_amount = DecimalField(
        "How much is it rented for? (monthly $)", validators=[Optional(), Length(max=64)]
    )

    listed_with_realtor = SelectField(
        "4) Is the property listed with a realtor?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[DataRequired(message="Please tell us if it’s listed.")],
    )
    list_price = DecimalField("If listed, how much is it listed for?", validators=[Optional()])

    condition = SelectField(
        "5) On a scale of 1 to 10, what would you say the condition is?",
        choices=[("", "— Select —")] + [(str(i), str(i)) for i in range(1, 11)],
        validators=[DataRequired(message="Please select the condition (1–10).")],
        default=""
    )

    submit = SubmitField("Continue")


# --- Step 3 (FOLLOW-UP: repairs, financials, motivation, etc.) ---
class LeadStep3MoreForm(FlaskForm):
    # No multifamily fields here. Just the financial & follow-up questions.
    repairs_needed   = SelectField("6) What repairs are needed?",
        choices=REPAIR_LEVEL_CHOICES, validators=[Optional()])
    repairs_cost_est = IntegerField(
    "7) Estimated repair cost ($)",
    validators=[Optional()])

    worth_estimate   = SelectField("8) What do you think the property is worth?", 
        choices=WORTH_CHOICES, validators=[Optional()])

    behind_on_payments = SelectField("9) Are you behind on payments?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    behind_amount   = IntegerField("If yes, how much are you behind?", validators=[Optional()])
    loan_balance    = IntegerField("What’s the balance on the loan?", validators=[Optional()])
    monthly_payment = IntegerField("Monthly payment ($)", validators=[Optional()])
    interest_rate   = StringField("Interest rate (%)", validators=[Optional()])

    will_sell_for_amount_owed = SelectField("10) Would you sell for the amount owed?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    
    how_much_owed = DecimalField("10a) How much do you owe?", validators=[Optional()])

    in_bankruptcy  = SelectField("11) Are you in bankruptcy?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    lowest_amount  = IntegerField("12) What’s the lowest amount you’d accept?", validators=[Optional()])
    flexible_price = SelectField("13) Are you flexible on that price?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    seller_finance_interest = SelectField("14) Would you consider seller financing?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    title_others = SelectField(
        "15) Is anyone else on title?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()]
    )

    title_others_names = StringField(
        "Who is on title?",
        validators=[Optional()]
    )
    title_others_willing = SelectField("Are they willing to sell?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    how_hear_about_us = SelectField("16) How did you hear about us?",
        choices=[("", "— Select —"),
            ("google", "Google"),
            ("facebook", "Facebook"),
            ("referral", "Referral"),
            ("postcard", "Postcard"),
            ("bandit_sign", "Bandit Sign"),
            ("other", "Other")],
        validators=[Optional()])
    how_hear_other = StringField("If Other, please specify", validators=[Optional()])

    notes = TextAreaField("Anything else we need to know?", validators=[Optional()])
    attachments = MultipleFileField("Attachments (images, video, docs)", validators=[Optional()])
    intake_payload = HiddenField(validators=[Optional()])

    submit = SubmitField("Submit")

class UpdateStatusForm(FlaskForm):
    lead_status = SelectField(
        "Lead Status",
        choices=[
            ("New Lead", "New Lead"),
            ("Contacted", "Contacted"),
            ("Appointment Set", "Appointment Set"),
            ("Offer Made", "Offer Made"),
            ("Under Contract", "Under Contract"),
            ("Closed", "Closed"),
            ("Dead", "Dead"),
        ],
        validators=[DataRequired()],
    )
    submit = SubmitField("Update Status")


class BuyerStep1Form(FlaskForm):
    first_name = StringField("First Name", validators=[DataRequired()])
    last_name  = StringField("Last Name", validators=[Optional()])
    email      = StringField("Email", validators=[Email(), DataRequired()])
    phone      = StringField("Phone Number", validators=[DataRequired()])
    city_focus = StringField("Primary City You Invest In", validators=[DataRequired()])
    submit     = SubmitField("Continue")


class BuyerStep2Form(FlaskForm):
    zip_codes         = StringField("Target ZIP codes (comma-separated)", validators=[Optional()])
    property_types    = StringField("Property types (e.g., Single Family, Duplex)", validators=[Optional()])
    max_repairs_level = SelectField(
        "Repairs you’re willing to take on",
        choices=[("", "— Select —"), ("light", "Light"), ("medium", "Medium"), ("heavy", "Heavy")],
        validators=[Optional()],
    )
    max_budget = StringField("Max purchase price ($)", validators=[Optional()])
    min_beds   = StringField("Minimum bedrooms", validators=[Optional()])
    min_baths  = StringField("Minimum bathrooms", validators=[Optional()])
    notes      = TextAreaField("Notes", validators=[Optional()])
    submit     = SubmitField("Submit")
