import os
import json
from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, current_app, session
)
from werkzeug.utils import secure_filename

from .models import Property
from.services.investor_snapshot import build_snapshot_for_property
from . import db
from .forms import (
    LeadStep1Form, LeadStep2Form, UpdateStatusForm,
    BuyerStep1Form, BuyerStep2Form, PropertyForm
)
from .models import Lead, Buyer, Property
from .services.property_eval import evaluate_property
from .services.zillow_client import evaluate_address_with_marketdata, ZillowError, search_address_for_zpid, property_details_by_zpid, normalize_details

main = Blueprint('main', __name__)

@main.route("/eval/address", methods=["GET", "POST"])
def eval_address():
    if request.method == "POST":
        # take structured values if present, else the visible field
        address = (request.form.get("full_address")
                   or request.form.get("address") or "").strip()
        if not address:
            flash("Please enter an address.", "warning")
            return redirect(url_for("main.eval_address"))

        api_key = current_app.config.get("RAPIDAPI_KEY", "")
        host    = current_app.config.get("ZILLOW_HOST", "zillow-com1.p.rapidapi.com")

        try:
            result = evaluate_address_with_marketdata(address, api_key, host)
            return render_template("eval_result.html", result=result)
        except ZillowError as ze:
            current_app.logger.exception("Zillow error")
            flash(f"Zillow API error: {ze}", "danger")
        except Exception as e:
            current_app.logger.exception("Unexpected eval error")
            flash(f"Unexpected error: {e}", "danger")

        return redirect(url_for("main.eval_address"))

    # GET — pass Maps key so the eval_form autocomplete works
    return render_template(
        "eval_form.html",
        GOOGLE_MAPS_API_KEY=current_app.config.get("GOOGLE_MAPS_API_KEY", "")
    )

def allowed_file(filename: str) -> bool:
    ext = (filename.rsplit('.', 1)[1].lower() if '.' in filename else '')
    return ext in current_app.config['ALLOWED_IMAGE_EXTENSIONS']

@main.route('/')
def home():
    return redirect(url_for('main.leads_list'))

# ---------- LEADS ----------

@main.route('/lead_step1', methods=['GET', 'POST'])
def lead_new_step1():
    form = LeadStep1Form()
    if form.validate_on_submit():
        session['lead_step1'] = {
            'seller_first_name': form.seller_first_name.data.strip(),
            'seller_last_name':  (form.seller_last_name.data or '').strip(),
            'email':             form.email.data.strip(),
            'phone':             form.phone.data.strip(),
            'address':           form.address.data.strip(),
            'full_address':      form.full_address.data or form.address.data.strip(),
            'lat':               form.lat.data or None,
            'lng':               form.lng.data or None,
        }
        return redirect(url_for('main.lead_new_step2'))

    # 👇 pass key from config to template
    return render_template(
        'lead_step1.html',
        form=form,
        GOOGLE_MAPS_API_KEY=current_app.config.get('GOOGLE_MAPS_API_KEY', '')
    )

@main.route('/lead/new/step2', methods=['GET', 'POST'])
def lead_new_step2():
    step1 = session.get('lead_step1')
    if not step1:
        flash("Please complete Step 1 first.", "warning")
        return redirect(url_for('main.lead_new_step1'))  # <- correct endpoint

    form = LeadStep2Form()
    if form.validate_on_submit():
        # photos
        saved_files = []
        upload_dir = current_app.config['UPLOAD_FOLDER']
        os.makedirs(upload_dir, exist_ok=True)
        for file in request.files.getlist('photos') or []:
            if file and file.filename:
                if allowed_file(file.filename):
                    filename = secure_filename(file.filename)
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

        # create lead
        lead = Lead(
            seller_first_name=step1['seller_first_name'],
            seller_last_name= step1['seller_last_name'],
            email=step1['email'],
            phone=step1['phone'],
            address=step1['address'],
            occupancy_status=form.occupancy_status.data or None,
            closing_date=(form.closing_date.data.isoformat() if form.closing_date.data else None),
            condition=form.condition.data,
            reason=form.reason.data,
            timeline=form.timeline.data,
            asking_price=form.asking_price.data,
            property_type=form.property_type.data,
            notes=form.notes.data,
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

        # auto-create property
        full_address = step1.get('full_address') or lead.address
        lat = step1.get('lat'); lng = step1.get('lng')
        try:
            lat = float(lat) if lat not in (None, "", "None") else None
            lng = float(lng) if lng not in (None, "", "None") else None
        except Exception:
            lat = None; lng = None

        prop = Property(
            address=lead.address,
            full_address=full_address,
            lat=lat,
            lng=lng,
            source="from_lead",
        )
        db.session.add(prop)
        db.session.commit()

        # ---- attempt auto-evaluation (best-effort; won’t break the flow) ----
        google_key = current_app.config.get('GOOGLE_MAPS_API_KEY', '')
        rapid_key  = current_app.config.get('RAPIDAPI_KEY', '')
        host       = current_app.config.get('ZILLOW_HOST', '')

        try:
            res = evaluate_property(
                prop.address, prop.full_address, prop.lat, prop.lng,
                google_key, rapid_key,
            )

            # save results
            prop.lat = res.get("lat")
            prop.lng = res.get("lng")
            prop.full_address = res.get("full_address") or prop.full_address

            facts = res.get("facts") or {}
            prop.zpid = facts.get("zpid")
            prop.beds = facts.get("beds")
            prop.baths = facts.get("baths")
            prop.sqft = facts.get("sqft")
            prop.lot_size = facts.get("lot_size")
            prop.year_built = facts.get("year_built")
            prop.school_district = facts.get("school_district")

            prop.arv_estimate = res.get("arv")
            prop.comps_json = json.dumps(res.get("comps") or [])
            prop.raw_json = json.dumps(facts.get("raw") or {})

            db.session.commit()
        except Exception as e:
            current_app.logger.exception(f"Auto-evaluate property from lead failed: {e}")

        session.pop('lead_step1', None)
        flash("Thanks! Your information has been submitted.", "success")
        return redirect(url_for('main.thank_you'))

    if request.method == 'POST' and not form.validate():
        current_app.logger.error(f"Step2 validation errors: {form.errors}")
        for field_name, errs in form.errors.items():
            if errs:
                flash(f"{field_name.replace('_',' ').title()}: {errs[0]}", "warning")

    return render_template('lead_step2.html', form=form)

@main.route('/properties/<int:property_id>/refresh', methods=['GET','POST'])
def property_refresh(property_id):
    prop = Property.query.get_or_404(property_id)
    addr = prop.full_address or prop.address
    rapid_key = current_app.config.get('RAPIDAPI_KEY', '')
    # host for details/search (can be same or different provider from marketData)
    details_host = current_app.config.get('PROPERTY_HOST', current_app.config.get('ZILLOW_HOST', ''))

    try:
        # 1) Ensure we have a zpid
        zpid = prop.zpid
        if not zpid:
            addr = prop.full_address or prop.address
            zpid = search_address_for_zpid(addr, rapid_key, details_host)
            if zpid:
                prop.zpid = zpid

        # 2) If we have zpid, get details and map into DB
        if zpid:
            raw_details = property_details_by_zpid(zpid, rapid_key, details_host)
            facts = normalize_details(raw_details)

            prop.full_address    = facts.get("fullAddress") or prop.full_address
            prop.beds            = facts.get("bedrooms") or prop.beds
            prop.baths           = facts.get("bathrooms") or prop.baths
            prop.sqft            = facts.get("livingArea") or prop.sqft
            prop.lot_size        = facts.get("lotSize") or prop.lot_size
            prop.year_built      = facts.get("yearBuilt") or prop.year_built
            prop.school_district = facts.get("schoolDistrict") or prop.school_district

            # optional: set lat/lng if missing
            if not prop.lat and facts.get("lat"):   prop.lat  = facts["lat"]
            if not prop.lng and facts.get("lng"):   prop.lng  = facts["lng"]

            # keep raw for debugging
            # (separate from your ZIP market raw_json if you like)
            # prop.raw_json = json.dumps(raw_details)

        # 3) Keep your ZIP market refresher if you want both views
        # md = market_data_by_zip(...)  # you already wired this part

        db.session.commit()
        flash("Property re-evaluated.", "success")

    except ZillowError as ze:
        current_app.logger.exception("Zillow error during property refresh")
        flash(f"Re-evaluation failed: {ze}", "warning")
    except Exception as e:
        current_app.logger.exception("Property re-eval failed")
        flash("Re-evaluation failed. Check API keys/paths and try again.", "warning")

    return redirect(url_for('main.property_detail', property_id=prop.id))


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

# ---------- BUYERS ----------

@main.route('/buyers/new/step1', methods=['GET','POST'])
def buyer_new_step1():
    form = BuyerStep1Form()
    if form.validate_on_submit():
        session['buyer_step1'] = {
            'first_name': form.first_name.data.strip(),
            'last_name':  (form.last_name.data or '').strip(),
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

# ---------- PROPERTIES ----------

@main.route('/properties')
def properties_list():
    q = request.args.get('q','').strip()
    query = Property.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Property.address.ilike(like)) |
            (Property.full_address.ilike(like)) |
            (Property.school_district.ilike(like))
        )
    props = query.order_by(Property.id.desc()).all()
    return render_template('properties_list.html', props=props, q=q)

@main.route('/properties/new', methods=['GET','POST'])
def property_new():
    form = PropertyForm()
    if form.validate_on_submit():
        prop = Property(
            address=form.address.data.strip(),
            full_address=(form.full_address.data or form.address.data.strip()),
            lat=(float(form.lat.data) if form.lat.data else None),
            lng=(float(form.lng.data) if form.lng.data else None),
            source="manual",
        )
        db.session.add(prop)
        db.session.commit()

        google_key = current_app.config.get('GOOGLE_MAPS_API_KEY', '')
        rapid_key  = current_app.config.get('RAPIDAPI_KEY', '')
        
        try:
            res = evaluate_property(prop.address, prop.full_address, prop.lat, prop.lng, google_key, rapid_key)
            prop.lat = res.get("lat"); prop.lng = res.get("lng"); prop.full_address = res.get("full_address") or prop.full_address
            facts = res.get("facts") or {}
            prop.zpid = facts.get("zpid")
            prop.beds = facts.get("beds"); prop.baths = facts.get("baths")
            prop.sqft = facts.get("sqft"); prop.lot_size = facts.get("lot_size")
            prop.year_built = facts.get("year_built"); prop.school_district = facts.get("school_district")
            prop.arv_estimate = res.get("arv")
            prop.comps_json = json.dumps(res.get("comps") or [])
            prop.raw_json = json.dumps(facts.get("raw") or {})
            db.session.commit()
        except Exception as e:
            current_app.logger.exception(f"Property evaluation failed: {e}")
            flash("Saved property, but auto-evaluation failed. Configure API keys and try again.", "warning")

        return redirect(url_for('main.property_detail', property_id=prop.id))

    return render_template(
        'property_form.html',
        form=form,
        GOOGLE_MAPS_API_KEY=current_app.config.get('GOOGLE_MAPS_API_KEY', '')
    )

@main.route('/properties/<int:property_id>')
def property_detail(property_id: int):
    prop = Property.query.get_or_404(property_id)

    snapshot = build_snapshot_for_property(prop)
    comps = []
    market = {}
    try:
        if prop.comps_json:
            comps = json.loads(prop.comps_json)
    except Exception:
        comps = []

    try:
        if prop.raw_json:
            market = json.loads(prop.raw_json)
    except Exception:
        market = {}

    return render_template(
        'property_detail.html',
        prop=prop,
        comps=comps,
        market=market,  # <-- pass parsed dict
        snapshot=snapshot or {},
        GOOGLE_MAPS_API_KEY=current_app.config.get('GOOGLE_MAPS_API_KEY', '')
    )


@main.route('/properties/<int:property_id>/delete', methods=['POST'])
def delete_property(property_id):
    prop = Property.query.get_or_404(property_id)
    db.session.delete(prop)
    db.session.commit()
    flash("Property deleted.", "info")
    return redirect(url_for('main.properties_list'))
