from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SelectField, SubmitField, DateField
from wtforms.validators import DataRequired, Optional, Email
from wtforms.fields import MultipleFileField

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

class LeadStep1Form(FlaskForm):
    seller_first_name = StringField("First Name", validators=[DataRequired()])
    seller_last_name  = StringField("Last Name", validators=[Optional()])
    email             = StringField("Email", validators=[Email(), DataRequired()])
    phone             = StringField("Phone Number", validators=[DataRequired()])

    # This will be connected to Google Places Autocomplete
    address           = StringField("Property Address", validators=[DataRequired()])

    submit = SubmitField("Continue")

class LeadStep2Form(FlaskForm):
    # NEW
    occupancy_status = SelectField("Is the property vacant, owner occupied, or rented out?",
                                   choices=OCCUPANCY_CHOICES, validators=[Optional()])

    # REQUIRED overall condition 1–10
    condition = SelectField("Overall condition (1–10) *",
                            choices=[(str(i), str(i)) for i in range(1, 11)],
                            validators=[DataRequired(message="Please rate the property condition 1–10.")])

    # Optional
    closing_date  = DateField("When is the closing date?", format="%Y-%m-%d", validators=[Optional()])
    timeline      = SelectField("Timeline to Sell", choices=[
        ("", "— Select —"),
        ("ASAP", "ASAP"),
        ("30 Days", "30 Days"),
        ("90 Days", "90 Days"),
        ("Just Curious", "Just Curious")
    ], validators=[Optional()])
    property_type = SelectField("Property Type", choices=[
        ("", "— Select —"),
        ("Single Family", "Single Family"),
        ("Duplex", "Duplex"),
        ("Vacant Lot", "Vacant Lot"),
        ("Other", "Other")
    ], validators=[Optional()])
    asking_price  = StringField("Asking Price", validators=[Optional()])
    reason        = TextAreaField("Reason for Selling", validators=[Optional()])
    notes         = TextAreaField("Notes", validators=[Optional()])

    # Repairs (all optional, dropdowns)
    ac_status           = SelectField("AC", choices=REPAIR_CHOICES, validators=[Optional()])
    roof_status         = SelectField("Roof", choices=REPAIR_CHOICES, validators=[Optional()])
    foundation_status   = SelectField("Foundation", choices=REPAIR_CHOICES, validators=[Optional()])
    water_heater_status = SelectField("Water heater", choices=REPAIR_CHOICES, validators=[Optional()])
    electrical_status   = SelectField("Electrical", choices=REPAIR_CHOICES, validators=[Optional()])
    plumbing_status     = SelectField("Plumbing", choices=REPAIR_CHOICES, validators=[Optional()])

    photos = MultipleFileField("Upload Photos (optional)")
    submit = SubmitField("Submit")

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
