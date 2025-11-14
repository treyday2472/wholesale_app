from flask_wtf import FlaskForm
from wtforms import (
    StringField, TextAreaField, SelectField, SubmitField, HiddenField
)
from wtforms.validators import DataRequired, Optional, Email
from wtforms.fields import MultipleFileField


# Optional helper choices
REPAIR_LEVEL_CHOICES = [
    ("", "— Select —"),
    ("cosmetics", "Cosmetics"),
    ("light", "Light repairs"),
    ("heavy", "Heavy repairs"),
    ("complete_rehab", "Complete rehab"),
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


    submit = SubmitField("Continue")



# --- Step 2 (CORE ONLY) ---
class LeadStep2CoreForm(FlaskForm):
    why_sell = TextAreaField("1) Why do you want to sell?", validators=[Optional()])

    property_type = SelectField(
        "2) What type of property is it?",
        choices=[
            ("", "— Select —"),
            ("single_family", "Single Family Home"),
            ("duplex", "Duplex"),
            ("condo", "Condo/Townhome"),
            ("land", "Land"),
            ("mobile", "Mobile/Manufactured Home"),
            ("other", "Other"),
        ],
        validators=[DataRequired(message="Please choose a property type.")],
    )

    occupancy_status = SelectField(
        "3) Is it owner occupied, rented out, or vacant?",
        choices=[("", "— Select —"),
                 ("owner_occupied", "Owner occupied"),
                 ("rented", "Rented out"),
                 ("vacant", "Vacant")],
        validators=[DataRequired(message="Please choose an occupancy status.")],
    )

    listed_with_realtor = SelectField(
        "4) Is the property listed with a realtor?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[DataRequired(message="Please tell us if it’s listed.")],
    )
    list_price = StringField("If listed, how much is it listed for?", validators=[Optional()])

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
    repairs_cost_est = StringField("7) How much will those repairs cost?", validators=[Optional()])
    worth_estimate   = StringField("8) What do you think the property is worth?", validators=[Optional()])

    behind_on_payments = SelectField("9) Are you behind on payments?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    behind_amount   = StringField("If yes, how much are you behind?", validators=[Optional()])
    loan_balance    = StringField("What’s the balance on the loan?", validators=[Optional()])
    monthly_payment = StringField("Monthly payment ($)", validators=[Optional()])
    interest_rate   = StringField("Interest rate (%)", validators=[Optional()])

    will_sell_for_amount_owed = SelectField("Would you sell for the amount owed?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    in_bankruptcy  = SelectField("Are you in bankruptcy?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    lowest_amount  = StringField("What’s the lowest amount you’d accept?", validators=[Optional()])
    flexible_price = SelectField("Are you flexible on that price?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    seller_finance_interest = SelectField("Would you consider seller financing?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    title_others         = StringField("Who else is on title?", validators=[Optional()])
    title_others_willing = SelectField("Are they willing to sell?",
        choices=[("", "— Select —"), ("yes", "Yes"), ("no", "No")],
        validators=[Optional()])
    how_hear_about_us = SelectField("How did you hear about us?",
        choices=[("", "— Select —"),
            ("google", "Google"),
            ("facebook", "Facebook"),
            ("referral", "Referral"),
            ("postcard", "Postcard"),
            ("bandit_sign", "Bandit Sign"),
            ("other", "Other")],
        validators=[Optional()])
    how_hear_other = StringField("If Other, please specify", validators=[Optional()])

    notes = TextAreaField("Notes", validators=[Optional()])
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
