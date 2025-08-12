from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField, SubmitField, DateField
from wtforms.validators import DataRequired, Optional, Email
from wtforms.fields import MultipleFileField
from wtforms import HiddenField

REPAIR_CHOICES = [
    ("", "— Select —"),
    ("needs_replacement", "Needs replacement"),
    ("average", "Average"),
    ("recently_replaced", "Recently replaced"),
]

OCCUPANCY_CHOICES = [
    ("", "— Select —"),
    ("vacant", "Vacant"),
    ("owner_occupied", "Owner occupied"),
    ("rented", "Rented out"),
]

class PropertyForm(FlaskForm):
    address      = StringField("Property Address", validators=[DataRequired()])
    full_address = HiddenField()
    lat          = HiddenField()
    lng          = HiddenField()
    submit       = SubmitField("Save & Evaluate")

class LeadStep1Form(FlaskForm):
    seller_first_name = StringField("First Name", validators=[DataRequired()])
    seller_last_name  = StringField("Last Name", validators=[Optional()])
    email             = StringField("Email", validators=[Email(), DataRequired()])
    phone             = StringField("Phone Number", validators=[DataRequired()])

    # This will be connected to Google Places Autocomplete
    address           = StringField("Property Address", validators=[DataRequired()])
    full_address = HiddenField()
    lat = HiddenField()
    lng = HiddenField()

    submit = SubmitField("Continue")


class LeadStep2Form(FlaskForm):
    # --- Step 2 (intake) new fields ---
    why_sell         = TextAreaField("1) Why do you want to sell?", validators=[DataRequired()])
    occupancy_status = SelectField("2) Is it owner occupied, rented out, or vacant?",
                                   choices=[("", "— Select —"),
                                            ("owner_occupied","Owner occupied"),
                                            ("rented","Rented out"),
                                            ("vacant","Vacant")],
                                   validators=[DataRequired()])
    rent_amount      = StringField("If rented, what is it rented for? (monthly $)", validators=[Optional()])
    is_multifam      = SelectField("2a) Is it multifamily?", choices=[("", "— Select —"),("yes","Yes"),("no","No")], validators=[Optional()])
    units_count      = StringField("2b) How many units are in the building?", validators=[Optional()])
    unit_rents_json  = HiddenField()  # 2c) JSON list of per-unit rents, built client-side
    vacant_units     = StringField("2d) How many units are vacant right now?", validators=[Optional()])

    listed_with_realtor = SelectField("3) Is the property listed with a realtor?",
                                      choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                      validators=[DataRequired()])
    list_price       = StringField("3a) If listed, how much is it listed for?", validators=[Optional()])

    condition        = SelectField("4) On a scale of 1 to 10, what would you say the condition is?",
                                   choices=[(str(i), str(i)) for i in range(1,11)],
                                   validators=[DataRequired()])
    repairs_needed   = TextAreaField("5) What repairs are needed?", validators=[Optional()])
    repairs_cost_est = StringField("6) How much do you think the repairs will run and why?", validators=[Optional()])
    worth_estimate   = StringField("6) What do you think the property is worth?", validators=[Optional()])

    behind_on_payments = SelectField("7) Are you behind on payments?",
                                     choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                     validators=[DataRequired()])
    behind_amount    = StringField("8a) If so, how much are you behind?", validators=[Optional()])
    loan_balance     = StringField("8b) What’s the balance on the loan?", validators=[Optional()])
    monthly_payment  = StringField("8c) What’s the monthly payment?", validators=[Optional()])
    interest_rate    = StringField("8d) What’s the interest rate?", validators=[Optional()])

    will_sell_for_amount_owed = SelectField("9) Will you be willing to sell for the amount owed?",
                                            choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                            validators=[DataRequired()])
    in_bankruptcy    = SelectField("10) Are you in bankruptcy?",
                                   choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                   validators=[DataRequired()])

    lowest_amount    = StringField("11) What is the lowest amount you would accept?", validators=[Optional()])
    flexible_price   = SelectField("12) Are you flexible on that price?",
                                   choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                   validators=[DataRequired()])

    seller_finance_interest = SelectField("13) Would you be interested in seller financing?",
                                          choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                          validators=[DataRequired()])

    title_others     = StringField("14) Who else is on title?", validators=[Optional()])
    title_others_willing = SelectField("Are they willing to sell?",
                                       choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                       validators=[Optional()])

    how_hear_about_us = SelectField("15) How did you hear about us?",
                                    choices=[("", "— Select —"),
                                             ("google","Google"),
                                             ("facebook","Facebook"),
                                             ("referral","Referral"),
                                             ("postcard","Postcard"),
                                             ("bandit_sign","Bandit Sign"),
                                             ("other","Other")],
                                    validators=[DataRequired()])
    how_hear_other   = StringField("If Other, please specify", validators=[Optional()])

    # Keep existing fields so route doesn't break
    notes            = TextAreaField("Notes", validators=[Optional()])
    photos           = MultipleFileField("Upload Photos (png, jpg, jpeg, webp)", validators=[Optional()])

    intake_payload   = HiddenField()  # JSON of all above for saving
    submit           = SubmitField("Submit")
class UpdateStatusForm(FlaskForm):
    lead_status = SelectField("Lead Status", choices=[
        ("New Lead", "New Lead"),
        ("Contacted", "Contacted"),
        ("Appointment Set", "Appointment Set"),
        ("Offer Made", "Offer Made"),
        ("Under Contract", "Under Contract"),
        ("Closed", "Closed"),
        ("Dead", "Dead")
    ])
    submit = SubmitField("Update Status")

class BuyerStep1Form(FlaskForm):
    first_name = StringField("First Name", validators=[DataRequired()])
    last_name  = StringField("Last Name", validators=[Optional()])
    email      = StringField("Email", validators=[Email(), DataRequired()])
    phone      = StringField("Phone Number", validators=[DataRequired()])
    city_focus = StringField("Primary City You Invest In", validators=[DataRequired()])
    submit     = SubmitField("Continue")

class BuyerStep2Form(FlaskForm):
    zip_codes      = StringField("Target ZIP codes (comma-separated)", validators=[Optional()])
    property_types = StringField("Property types (e.g., Single Family, Duplex)", validators=[Optional()])
    max_repairs_level = SelectField("Repairs you’re willing to take on",
                                    choices=[("", "— Select —"),("light","Light"),("medium","Medium"),("heavy","Heavy")],
                                    validators=[Optional()])
    max_budget     = StringField("Max purchase price ($)", validators=[Optional()])
    min_beds       = StringField("Minimum bedrooms", validators=[Optional()])
    min_baths      = StringField("Minimum bathrooms", validators=[Optional()])
    notes          = TextAreaField("Notes", validators=[Optional()])
    submit         = SubmitField("Submit")


class LeadStep2Form(FlaskForm):
    # --- Step 2 (intake) new fields ---
    why_sell         = TextAreaField("1) Why do you want to sell?", validators=[DataRequired()])
    occupancy_status = SelectField("2) Is it owner occupied, rented out, or vacant?",
                                   choices=[("", "— Select —"),
                                            ("owner_occupied","Owner occupied"),
                                            ("rented","Rented out"),
                                            ("vacant","Vacant")],
                                   validators=[DataRequired()])
    rent_amount      = StringField("If rented, what is it rented for? (monthly $)", validators=[Optional()])
    is_multifam      = SelectField("2a) Is it multifamily?", choices=[("", "— Select —"),("yes","Yes"),("no","No")], validators=[Optional()])
    units_count      = StringField("2b) How many units are in the building?", validators=[Optional()])
    unit_rents_json  = HiddenField()  # 2c) JSON list of per-unit rents, built client-side
    vacant_units     = StringField("2d) How many units are vacant right now?", validators=[Optional()])

    listed_with_realtor = SelectField("3) Is the property listed with a realtor?",
                                      choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                      validators=[DataRequired()])
    list_price       = StringField("3a) If listed, how much is it listed for?", validators=[Optional()])

    condition        = SelectField("4) On a scale of 1 to 10, what’s the condition?",
                                   choices=[(str(i), str(i)) for i in range(1,11)],
                                   validators=[DataRequired()])
    repairs_needed   = TextAreaField("5) What repairs are needed?", validators=[Optional()])
    repairs_cost_est = StringField("6) How much do you think repairs will run and why?", validators=[Optional()])
    worth_estimate   = StringField("7) What do you think the property is worth?", validators=[Optional()])

    behind_on_payments = SelectField("8) Are you behind on payments?",
                                     choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                     validators=[DataRequired()])
    behind_amount    = StringField("8a) If so, how much are you behind?", validators=[Optional()])
    loan_balance     = StringField("8b) What’s the balance on the loan?", validators=[Optional()])
    monthly_payment  = StringField("8c) What’s the monthly payment?", validators=[Optional()])
    interest_rate    = StringField("8d) What’s the interest rate?", validators=[Optional()])

    will_sell_for_amount_owed = SelectField("9) Will you be willing to sell for the amount owed?",
                                            choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                            validators=[DataRequired()])
    in_bankruptcy    = SelectField("10) Are you in bankruptcy?",
                                   choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                   validators=[DataRequired()])

    lowest_amount    = StringField("11) What is the lowest amount you would accept?", validators=[Optional()])
    flexible_price   = SelectField("12) Are you flexible on that price?",
                                   choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                   validators=[DataRequired()])

    seller_finance_interest = SelectField("13) Would you be interested in seller financing?",
                                          choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                          validators=[DataRequired()])

    title_others     = StringField("14) Who else is on title?", validators=[Optional()])
    title_others_willing = SelectField("Are they willing to sell?",
                                       choices=[("", "— Select —"),("yes","Yes"),("no","No")],
                                       validators=[Optional()])

    how_hear_about_us = SelectField("15) How did you hear about us?",
                                    choices=[("", "— Select —"),
                                             ("google","Google"),
                                             ("facebook","Facebook"),
                                             ("referral","Referral"),
                                             ("postcard","Postcard"),
                                             ("bandit_sign","Bandit Sign"),
                                             ("other","Other")],
                                    validators=[DataRequired()])
    how_hear_other   = StringField("If Other, please specify", validators=[Optional()])

    notes            = TextAreaField("Notes", validators=[Optional()])
    photos           = MultipleFileField("Upload Photos (png, jpg, jpeg, webp)", validators=[Optional()])

    intake_payload   = HiddenField()  # JSON of all above for saving
    submit           = SubmitField("Submit")
