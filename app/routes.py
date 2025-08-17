# --- imports ---
import os
import json
from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, current_app, session
)

import re

from werkzeug.utils import secure_filename
from wtforms.validators import Optional  # relaxing validators after 3 tries

from . import db
from .models import Lead, Buyer, Property
from .forms import (
    LeadStep1Form, LeadStep2Form, UpdateStatusForm,
    BuyerStep1Form, BuyerStep2Form, PropertyForm
)

# Zillow (lightweight “basics” on create + refresh endpoint for details)
from .services.zillow_client import (
    zillow_basics,
    evaluate_address_with_marketdata, ZillowError,
    search_address_for_zpid, property_details_by_zpid, normalize_details
)

# Melissa (on-demand enrich)
from .services.melissa_client import (
    lookup_property, lookup_deeds,  # lookup_homes_by_owner optional
    normalize_property_record, MelissaHttpError
)

# (optional) Salesforce export
from .services.salesforce import upsert_lead, SalesforceAuthError, SalesforceApiError
from .services.investor_snapshot import build_snapshot_for_property

main = Blueprint('main', __name__)

def _split_us_address(addr: str):
    """Return (a1, city, state, postal) or (addr, '', '', '') if we can't parse."""
    if not addr:
        return "", "", "", ""
    addr = addr.strip()

    patterns = [
        r"^\s*(?P<a1>.+?)\s*,\s*(?P<city>[^,]+?)\s*,\s*(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$",   # street, city, ST ZIP
        r"^\s*(?P<a1>.+?)\s+(?P<city>[^,]+?)\s*,\s*(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$",       # street city, ST ZIP
        r"^\s*(?P<a1>.+?)\s+(?P<city>.+?)\s+(?P<state>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$",              # street city ST ZIP
    ]
    for p in patterns:
        m = re.match(p, addr)
        if m:
            return (m.group('a1'), m.group('city'), m.group('state').upper(), m.group('zip'))
    return (addr, "", "", "")


# ---------- EVAL ADDRESS (public) ----------
@main.route("/eval/address", methods=["GET", "POST"])
def eval_address():
    if request.method == "POST":
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
        return redirect(url_for('main.lead_new_step1'))

    form = LeadStep2Form()

    # Initialize attempts once
    if request.method == 'GET' and not (form.attempts_count.data or "").strip():
        form.attempts_count.data = '0'

    attempts = 0
    if request.method == 'POST':
        try:
            attempts = int(request.form.get('attempts_count', 0) or 0)
        except Exception:
            attempts = 0

        # Relax validators after 3 failed tries
        if attempts >= 3:
            for f in (form.occupancy_status, form.listed_with_realtor, form.condition):
                f.validators = [Optional()]

        if form.validate():
            # Use default condition "7" if user hit 3 tries and still left it blank
            cond_value = form.condition.data or ("7" if attempts >= 3 else None)

            lead = Lead(
                seller_first_name = step1['seller_first_name'],
                seller_last_name  = step1.get('seller_last_name') or None,
                email             = step1['email'],
                phone             = step1['phone'],
                address           = step1['address'],
                occupancy_status  = form.occupancy_status.data or None,
                condition         = cond_value,  # <-- use the default if needed
                notes             = (form.notes.data or '').strip() or None,
                lead_source       = "Web Form",
            )

            intake = {
                "why_sell": form.why_sell.data,
                "occupancy_status": form.occupancy_status.data,
                "rent_amount": form.rent_amount.data,
                "is_multifam": form.is_multifam.data,
                "units_count": form.units_count.data,
                "unit_rents_json": form.unit_rents_json.data,
                "vacant_units": form.vacant_units.data,
                "listed_with_realtor": form.listed_with_realtor.data,
                "list_price": form.list_price.data,
                "condition": cond_value,  # mirror the saved value
                "repairs_needed": form.repairs_needed.data,
                "repairs_cost_est": form.repairs_cost_est.data,
                "worth_estimate": form.worth_estimate.data,
                "behind_on_payments": form.behind_on_payments.data,
                "behind_amount": form.behind_amount.data,
                "loan_balance": form.loan_balance.data,
                "monthly_payment": form.monthly_payment.data,
                "interest_rate": form.interest_rate.data,
                "will_sell_for_amount_owed": form.will_sell_for_amount_owed.data,
                "in_bankruptcy": form.in_bankruptcy.data,
                "lowest_amount": form.lowest_amount.data,
                "flexible_price": form.flexible_price.data,
                "seller_finance_interest": form.seller_finance_interest.data,
                "title_others": form.title_others.data,
                "title_others_willing": form.title_others_willing.data,
                "how_hear_about_us": form.how_hear_about_us.data,
                "how_hear_other": form.how_hear_other.data,
            }
            lead.intake = intake

            db.session.add(lead)
            db.session.commit()

            # Handle uploads (attachments/photos)
            upload_dir = current_app.config.get('UPLOAD_FOLDER', os.path.join(current_app.static_folder, 'uploads'))
            os.makedirs(upload_dir, exist_ok=True)
            saved_files = []
            files = []
            if 'attachments' in request.files:
                files.extend(request.files.getlist('attachments'))
            if 'photos' in request.files:
                files.extend(request.files.getlist('photos'))
            for file in files:
                if not file or not getattr(file, 'filename', ''):
                    continue
                filename = secure_filename(file.filename)
                base, ext = os.path.splitext(filename)
                final = filename
                i = 1
                while os.path.exists(os.path.join(upload_dir, final)):
                    final = f"{base}_{i}{ext}"
                    i += 1
                file.save(os.path.join(upload_dir, final))
                saved_files.append(final)
            if saved_files:
                lead.image_files = ",".join(saved_files)
            db.session.commit()

            # Create a Property record (Zillow basics happen on manual property_new;
            # we keep lead flow simple)
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
                lat=lat, lng=lng,
                source="from_lead",
            )
            db.session.add(prop)
            db.session.commit()

            session.pop('lead_step1', None)
            flash("Thanks! Your information has been submitted.", "success")
            return redirect(url_for('main.thank_you'))

    # GET or invalid POST → re-render (form retains values & field errors)
    return render_template('lead_step2.html', form=form)

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

    intake = lead.intake or {}
    if isinstance(intake, str):
        try:
            intake = json.loads(intake)
        except Exception:
            intake = {}

    prop = (Property.query
            .filter_by(address=lead.address)
            .order_by(Property.id.desc())
            .first())

    if form.validate_on_submit():
        lead.lead_status = form.lead_status.data
        db.session.commit()
        flash("Lead status updated.", "success")
        return redirect(url_for('main.lead_detail', lead_id=lead.id))

    images = lead.image_files.split(",") if lead.image_files else []
    return render_template('lead_detail.html', lead=lead, form=form, images=images, intake=intake, prop=prop)

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

        # Pull quick Zillow-style basics on creation (no Melissa credit)
        try:
            basics = zillow_basics(prop.full_address or prop.address)
            if basics:
                prop.beds = basics.get("beds") or prop.beds
                prop.baths = basics.get("baths") or prop.baths
                prop.sqft = basics.get("sqft") or prop.sqft
                prop.year_built = basics.get("year_built") or prop.year_built

                raw = {}
                try:
                    raw = json.loads(prop.raw_json) if prop.raw_json else {}
                except Exception:
                    raw = {}
                raw["zillow"] = basics
                prop.raw_json = json.dumps(raw)
                db.session.commit()
        except Exception as e:
            current_app.logger.exception(f"Zillow basics failed: {e}")

        return redirect(url_for('main.property_detail', property_id=prop.id))

    return render_template(
        'property_form.html',
        form=form,
        GOOGLE_MAPS_API_KEY=current_app.config.get('GOOGLE_MAPS_API_KEY', '')
    )

@main.route('/properties/<int:property_id>')
def property_detail(property_id: int):
    prop = Property.query.get_or_404(property_id)

    # Build unified snapshot (your existing helper)
    snapshot = build_snapshot_for_property(prop) or {}

    # Parse raw_json once and pass it through as "raw"
    try:
        raw = json.loads(prop.raw_json) if prop.raw_json else {}
    except Exception:
        raw = {}

    return render_template(
        'property_detail.html',
        prop=prop,
        snapshot=snapshot,
        raw=raw,  # <-- important: give the template access to the raw payloads
        GOOGLE_MAPS_API_KEY=current_app.config.get('GOOGLE_MAPS_API_KEY', '')
    )


@main.route('/properties/<int:property_id>/delete', methods=['POST'])
def delete_property(property_id):
    prop = Property.query.get_or_404(property_id)
    db.session.delete(prop)
    db.session.commit()
    flash("Property deleted.", "info")
    return redirect(url_for('main.properties_list'))

@main.route('/properties/<int:property_id>/refresh', methods=['GET','POST'])
def property_refresh(property_id):
    """
    Refresh Zillow details and also stash Zestimate / Rent Zestimate
    under raw_json["zillow"] so the template can always show them.
    """
    prop = Property.query.get_or_404(property_id)
    rapid_key = current_app.config.get('RAPIDAPI_KEY', '')
    details_host = current_app.config.get('PROPERTY_HOST', current_app.config.get('ZILLOW_HOST', ''))

    try:
        # 1) Ensure zpid
        zpid = prop.zpid
        if not zpid:
            addr = prop.full_address or prop.address
            if not addr:
                raise ZillowError("Missing address")
            zpid = search_address_for_zpid(addr, rapid_key, details_host)
            if zpid:
                prop.zpid = zpid

        # 2) Pull details → normalize into columns
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
            if not prop.lat and facts.get("lat"): prop.lat = facts["lat"]
            if not prop.lng and facts.get("lng"): prop.lng = facts["lng"]

            # 3) Try to fetch zestimate & rent quickly; stash into raw_json['zillow']
            # (Your zillow_basics already hits a lightweight endpoint)
            try:
                basics = zillow_basics(prop.full_address or prop.address)
                if basics:
                    raw = {}
                    try:
                        raw = json.loads(prop.raw_json) if prop.raw_json else {}
                    except Exception:
                        raw = {}
                    raw.setdefault("zillow", {})
                    # Keep anything present; prefer explicit zestimate keys from basics if available
                    for k in ("zestimate", "rent_zestimate", "bedrooms", "bathrooms", "sqft", "year_built"):
                        if basics.get(k) is not None:
                            raw["zillow"][k] = basics.get(k)
                    prop.raw_json = json.dumps(raw)
            except Exception:
                current_app.logger.info("zillow_basics failed; continuing")

        db.session.commit()
        flash("Property re-evaluated.", "success")

    except ZillowError as ze:
        current_app.logger.exception("Zillow error during property refresh")
        flash(f"Re-evaluation failed: {ze}", "warning")
    except Exception:
        current_app.logger.exception("Property re-eval failed")
        flash("Re-evaluation failed. Check API keys/paths and try again.", "warning")

    return redirect(url_for('main.property_detail', property_id=prop.id))

# ---------- SALESFORCE ----------
@main.route('/leads/<int:lead_id>/export_sf', methods=['POST'])
def export_lead_to_salesforce(lead_id):
    if not current_app.config.get("SF_ENABLED"):
        flash("Salesforce integration is disabled.", "warning")
        return redirect(url_for('main.lead_detail', lead_id=lead_id))

    lead = Lead.query.get_or_404(lead_id)
    payload = {
        "FirstName": lead.seller_first_name or "",
        "LastName":  (lead.seller_last_name or "Seller"),
        "Company":   "DealBot",
        "Phone":     lead.phone or "",
        "Email":     lead.email or "",
        "Street":    (lead.address or ""),
        "LeadSource": lead.lead_source or "DealBot",
        "Description": (json.dumps(lead.intake) if isinstance(lead.intake, (dict,list)) else str(lead.intake or ""))[:32000]
    }

    try:
        sf_id = upsert_lead(payload)
        if sf_id:
            lead.sf_lead_id = sf_id
            db.session.commit()
            flash(f"Exported to Salesforce (Lead Id: {sf_id}).", "success")
        else:
            flash("Exported/updated in Salesforce.", "success")
    except SalesforceAuthError:
        flash("Salesforce not connected. Click 'Connect Salesforce' first.", "danger")
    except SalesforceApiError as e:
        current_app.logger.exception("SF upsert failed")
        flash(f"Salesforce error: {e}", "danger")

    return redirect(url_for('main.lead_detail', lead_id=lead.id))

# ---------- MELISSA ENRICH (single, correct route) ----------
# ---------- MELISSA ENRICH (single, robust) ----------
@main.route('/properties/<int:property_id>/enrich-melissa', methods=['POST'])
def enrich_property_melissa(property_id):
    prop = Property.query.get_or_404(property_id)

    if not current_app.config.get('MELISSA_API_KEY') and not current_app.config.get('MELISSA_KEY'):
        flash("Melissa key not configured.", "warning")
        return redirect(url_for('main.property_detail', property_id=prop.id))

    # Build an ff (free-form) string; also try to parse for a1/city/state/postal for LookupProperty
    addr = prop.full_address or prop.address or ""
    a1, city, state, postal = addr, "", "", ""
    if "," in addr:
        try:
            line, tail = addr.split(",", 1)
            parts = tail.strip().split()
            # crude parse: CITY words then ST then ZIP
            st = parts[-2] if len(parts) >= 2 else ""
            z  = parts[-1] if parts and parts[-1].isdigit() else ""
            if z:
                city = " ".join(parts[:-2])
                state = st
                postal = z
            else:
                city = " ".join(parts[1:]) if len(parts) > 1 else parts[0]
                state = st
            a1 = line.strip()
        except Exception:
            a1 = addr

    try:
        # 1) Property look-up (to get Parcel/FIPS/APN + some basics)
        prop_payload = lookup_property(a1=a1, city=city, state=state, postal=postal, country="US")

        # Try to pull fips/apn for a stronger Deeds lookup
        recs = (prop_payload or {}).get("Records") or []
        fips = apn = None
        if recs:
            rec = recs[0]
            parcel = rec.get("Parcel") or {}
            size   = (rec.get("PropertySize") or {}).get("AreaLotSF")
            owner  = rec.get("PrimaryOwner") or {}
            oaddr  = rec.get("OwnerAddress") or {}
            legal_desc = rec.get("Legal") or {}


        # 2) Deeds look-up: prefer fips+apn; else ff
        deeds_payload = None
        try:
            if fips and apn:
                deeds_payload = lookup_deeds(fips=fips, apn=apn)
            else:
                deeds_payload = lookup_deeds(ff=addr)
        except MelissaHttpError as _mhe:
            # still keep going with just property payload
            current_app.logger.warning("LookupDeeds warning: %s", _mhe)

        # 3) Merge raw payloads
        raw = {}
        try: raw = json.loads(prop.raw_json) if prop.raw_json else {}
        except Exception: raw = {}

        raw.setdefault("melissa", {})
        raw["melissa"]["LookupProperty"] = prop_payload
        if deeds_payload is not None:
            raw["melissa"]["LookupDeeds"] = deeds_payload
        prop.raw_json = json.dumps(raw)

        # 4) Normalize and promote
        if recs:
            norm = normalize_property_record(recs[0], deeds_payload=deeds_payload)

            raw = json.loads(prop.raw_json or "{}")
            raw["ownership_mortgage"] = norm.get("ownership") or {}
            raw["2_classification"]   = norm.get("classification") or {}
            meta = raw.get("meta", {})
            meta.setdefault("sources", {}).update((norm.get("meta") or {}).get("sources", {}))
            meta.setdefault("as_of",   {}).update((norm.get("meta") or {}).get("as_of",   {}))
            raw["meta"] = meta
            prop.raw_json = json.dumps(raw)

            # Optionally fill empty DB structure fields
            s = norm.get("structure") or {}
            if (prop.beds or 0) == 0 and s.get("beds"):           prop.beds = s["beds"]
            if (prop.baths or 0) == 0 and s.get("baths"):         prop.baths = s["baths"]
            if (prop.sqft or 0) == 0 and s.get("sqft"):           prop.sqft = s["sqft"]
            if (prop.year_built or 0) == 0 and s.get("year_built"): prop.year_built = s["year_built"]

        db.session.commit()
        flash("Enriched from Melissa.", "success")

    except MelissaHttpError as mhe:
        current_app.logger.warning(f"Melissa enrich failed: {mhe}")
        flash("Melissa lookup failed. Check URL/key/params.", "warning")
    except Exception as e:
        current_app.logger.exception("Unexpected Melissa enrich error")
        flash("Unexpected error during Melissa enrich.", "danger")

    return redirect(url_for('main.property_detail', property_id=prop.id))

@main.route('/learn/seller-financing')
def learn_seller_financing():
    return render_template('learn_seller_financing.html')

# ---------- Aliases ----------
@main.route('/lead_form', methods=['GET'])
def lead_form_alias():
    return redirect(url_for('main.lead_new_step1'))
