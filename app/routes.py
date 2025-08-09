import os
from flask import Blueprint, render_template, redirect, url_for, request, flash, current_app, session
from werkzeug.utils import secure_filename
from .forms import LeadStep1Form, LeadStep2Form, UpdateStatusForm, BuyerStep1Form, BuyerStep2Form # keep UpdateStatusForm if you already added it
from .models import Lead
from . import db
from .models import Lead, Buyer




main = Blueprint('main', __name__)

def allowed_file(filename):
    ext = (filename.rsplit('.', 1)[1].lower() if '.' in filename else '')
    return ext in current_app.config['ALLOWED_IMAGE_EXTENSIONS']

# Make / the dashboard; tweak if you prefer
@main.route('/')
def home():
    return redirect(url_for('main.leads_list'))



# Step 1: vital contact info
@main.route('/lead_new_step1', methods=['GET', 'POST'])
def lead_new_step1():
    form = LeadStep1Form()
    if form.validate_on_submit():
        # stash in session; we’ll commit after step2
        session['lead_step1'] = {
            'seller_first_name': form.seller_first_name.data.strip(),
            'seller_last_name':  form.seller_last_name.data.strip() if form.seller_last_name.data else '',
            'email':             form.email.data.strip(),
            'phone':             form.phone.data.strip(),
            'address': form.address.data.strip(),
            'full_address': form.full_address.data or form.address.data.strip(),
            'lat': form.lat.data or None,
            'lng': form.lng.data or None,
        }
        return redirect(url_for('main.lead_new_step2'))
    return render_template('lead_step1.html', form=form)

# Step 2: details + photo upload
@main.route('/lead/new/step2', methods=['GET', 'POST'])
def lead_new_step2():
    # must have step1 data
    step1 = session.get('lead_step1')
    if not step1:
        flash("Please complete Step 1 first.", "warning")
        return redirect(url_for('main.lead_new_step1'))

    form = LeadStep2Form()

    if form.validate_on_submit():
        # ---- handle photos ----
        saved_files = []
        upload_dir = current_app.config['UPLOAD_FOLDER']
        os.makedirs(upload_dir, exist_ok=True)

        try:
            files = request.files.getlist('photos')
        except Exception:
            files = []

        for file in files:
            if file and file.filename:
                if allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    # avoid collisions
                    base, ext = os.path.splitext(filename)
                    final = filename
                    i = 1
                    while os.path.exists(os.path.join(upload_dir, final)):
                        final = f"{base}_{i}{ext}"
                        i += 1
                    file.save(os.path.join(upload_dir, final))
                    saved_files.append(final)
                else:
                    flash(f"Unsupported file type: {file.filename}", "warning")

        # ---- create Lead ----
        lead = Lead(
            seller_first_name=step1['seller_first_name'],
            seller_last_name= step1['seller_last_name'],
            email=step1['email'],
            phone=step1['phone'],
            address=step1['address'],

            # NEW fields
            occupancy_status=form.occupancy_status.data or None,
            closing_date=(form.closing_date.data.isoformat() if form.closing_date.data else None),

            # required + optional
            condition=form.condition.data,
            reason=form.reason.data,
            timeline=form.timeline.data,
            asking_price=form.asking_price.data,
            property_type=form.property_type.data,
            notes=form.notes.data,

            # repairs (optional)
            ac_status=form.ac_status.data or None,
            roof_status=form.roof_status.data or None,
            foundation_status=form.foundation_status.data or None,
            water_heater_status=form.water_heater_status.data or None,
            electrical_status=form.electrical_status.data or None,
            plumbing_status=form.plumbing_status.data or None,

            image_files=",".join(saved_files) if saved_files else None,
            lead_source="Web Form",
        )
        db.session.add(lead)
        db.session.commit()

        # clear multi-step cache
        session.pop('lead_step1', None)

        flash("Thanks! Your information has been submitted.", "success")
        return redirect(url_for('main.thank_you'))  # <-- go to thank-you page

    # If POST but not valid, surface errors so you can see what's wrong
    if request.method == 'POST' and not form.validate():
        # Log to console
        current_app.logger.error(f"Step2 validation errors: {form.errors}")
        # Show a single flash with first errors per field
        for field_name, errs in form.errors.items():
            if errs:
                flash(f"{field_name.replace('_',' ').title()}: {errs[0]}", "warning")

    return render_template('lead_step2.html', form=form)

# Existing views (dashboard, detail, delete) — keep yours; shown here for completeness:
@main.route('/leads')
def leads_list():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    query = Lead.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Lead.seller_first_name.ilike(like)) |
            (Lead.seller_last_name.ilike(like)) |
            (Lead.phone.ilike(like)) |
            (Lead.email.ilike(like)) |
            (Lead.address.ilike(like)) |
            (Lead.lead_source.ilike(like))
        )
    if status:
        query = query.filter(Lead.lead_status == status)
    leads = query.order_by(Lead.id.desc()).all()
    statuses = ["", "New Lead", "Contacted", "Appointment Set", "Offer Made", "Under Contract", "Closed", "Dead"]
    return render_template('leads_list.html', leads=leads, q=q, status=status, statuses=statuses)

@main.route('/leads/<int:lead_id>', methods=['GET', 'POST'])
def lead_detail(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    form = UpdateStatusForm(lead_status=lead.lead_status)
    if form.validate_on_submit():
        lead.lead_status = form.lead_status.data
        db.session.commit()
        flash("Lead status updated.", "success")
        return redirect(url_for('main.lead_detail', lead_id=lead.id))
    # split images for display
    images = (lead.image_files.split(",") if lead.image_files else [])
    return render_template('lead_detail.html', lead=lead, form=form, images=images)

@main.route('/leads/<int:lead_id>/delete', methods=['POST'])
def delete_lead(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    db.session.delete(lead)
    db.session.commit()
    flash("Lead deleted.", "info")
    return redirect(url_for('main.leads_list'))


@main.route('/thank_you')
def thank_you():
    return render_template('thank_you.html')

# ---- BUYERS ----

@main.route('/buyers/new/step1', methods=['GET','POST'])
def buyer_new_step1():
    form = BuyerStep1Form()
    if form.validate_on_submit():
        session['buyer_step1'] = {
            'first_name': form.first_name.data.strip(),
            'last_name':  form.last_name.data.strip() if form.last_name.data else '',
            'email':      form.email.data.strip(),
            'phone':      form.phone.data.strip(),
            'city_focus': form.city_focus.data.strip(),
        }
        return redirect(url_for('main.buyer_new_step2'))
    return render_template('buyer_step1.html', form=form)

@main.route('/buyers/new/step2', methods=['GET','POST'])
def buyer_new_step2():
    step1 = session.get('buyer_step1')
    if not step1:
        flash("Please complete Step 1 first.", "warning")
        return redirect(url_for('main.buyer_new_step1'))

    form = BuyerStep2Form()
    if form.validate_on_submit():
        buyer = Buyer(
            first_name=step1['first_name'],
            last_name =step1['last_name'],
            email=step1['email'],
            phone=step1['phone'],
            city_focus=step1['city_focus'],

            zip_codes=(form.zip_codes.data or '').strip() or None,
            property_types=(form.property_types.data or '').strip() or None,
            max_repairs_level=form.max_repairs_level.data or None,
            max_budget=(form.max_budget.data or '').strip() or None,
            min_beds=(form.min_beds.data or '').strip() or None,
            min_baths=(form.min_baths.data or '').strip() or None,
            notes=form.notes.data or None,
        )
        db.session.add(buyer)
        db.session.commit()
        session.pop('buyer_step1', None)
        flash("Buyer saved.", "success")
        return redirect(url_for('main.buyer_detail', buyer_id=buyer.id))
    return render_template('buyer_step2.html', form=form)

@main.route('/buyers')
def buyers_list():
    q = request.args.get('q','').strip()
    query = Buyer.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Buyer.first_name.ilike(like)) |
            (Buyer.last_name.ilike(like)) |
            (Buyer.phone.ilike(like)) |
            (Buyer.email.ilike(like)) |
            (Buyer.city_focus.ilike(like)) |
            (Buyer.zip_codes.ilike(like)) |
            (Buyer.property_types.ilike(like))
        )
    buyers = query.order_by(Buyer.id.desc()).all()
    return render_template('buyers_list.html', buyers=buyers, q=q)

@main.route('/buyers/<int:buyer_id>')
def buyer_detail(buyer_id):
    buyer = Buyer.query.get_or_404(buyer_id)
    return render_template('buyer_detail.html', buyer=buyer)

@main.route('/buyers/<int:buyer_id>/delete', methods=['POST'])
def delete_buyer(buyer_id):
    buyer = Buyer.query.get_or_404(buyer_id)
    db.session.delete(buyer)
    db.session.commit()
    flash("Buyer deleted.", "info")
    return redirect(url_for('main.buyers_list'))
