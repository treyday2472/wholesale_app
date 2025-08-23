# --- imports ---
import os
import json
from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, current_app, session, jsonify
)

from .services import attom as attom_svc
from datetime import datetime


from .services.attom import AttomError
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

def _log(raw: dict, *, source: str, event: str, status, note: str = None, meta: dict = None):
    raw.setdefault("log", []).append({
        "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "source": source,
        "event": event,
        "status": status,
        "note": note,
        "meta": meta or {}
    })

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

    lead_for_prop = (Lead.query.filter(Lead.address == prop.address).order_by(Lead.id.desc()).first())
    est_repairs = None
    if lead_for_prop:
        intake = lead_for_prop.intake
        if isinstance(intake, str):
            try: intake = json.loads(intake)
            except Exception: intake = {}
        if isinstance(intake, dict):
            est_repairs = intake.get("repairs_cost_est")

    # pull filtered comps saved by enrich_attom
    attx = (raw.get("attom_extract") or {})
    comps_list = attx.get("comps") or []    

    return render_template(
        'property_detail.html',
        prop=prop,
        snapshot=snapshot,
        est_repairs=est_repairs,
        comps_list=comps_list,

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

    # 0) Build address strings
    raw_addr = (prop.full_address or prop.address or "").strip()
    # strip trailing country tokens that confuse simple parsers
    raw_addr_clean = re.sub(r'\s*,?\s*(USA|United States)$', '', raw_addr, flags=re.I)

    # Use our helper to split; it handles several common formats
    a1, city, state, postal = _split_us_address(raw_addr_clean)

    # 1) LookupProperty — try structured first, then fall back to free-form
    prop_payload = None
    try:
        prop_payload = lookup_property(
            a1=a1, city=city, state=state, postal=postal,
            country="US", cols="GrpAll"
        )
        recs = (prop_payload or {}).get("Records") or []
        # If we only got a 'Results' stub (no Parcel/Legal/etc), retry with ff
        needs_ff = (not recs) or (len(recs) == 1 and list(recs[0].keys()) == ["Results"])
        if needs_ff:
            prop_payload = lookup_property(ff=raw_addr_clean, cols="GrpAll")
            recs = (prop_payload or {}).get("Records") or []
    except MelissaHttpError:
        # hard fallback to ff only
        prop_payload = lookup_property(ff=raw_addr_clean, cols="GrpAll")
        recs = (prop_payload or {}).get("Records") or []

    # 2) Derive strong keys for deeds
    fips = apn = None
    if recs:
        r0 = recs[0] or {}
        parcel = (r0.get("Parcel") or {})
        fips = parcel.get("FIPSCode")
        apn  = parcel.get("UnformattedAPN") or parcel.get("FormattedAPN")

    # 3) LookupDeeds (prefer FIPS+APN, else ff)
    deeds_payload = None
    try:
        if fips and apn:
            deeds_payload = lookup_deeds(fips=fips, apn=apn)
        else:
            deeds_payload = lookup_deeds(ff=raw_addr_clean)
    except MelissaHttpError as _:
        deeds_payload = None  # keep going with property payload

    # 4) Merge raw payloads into DB
    raw = {}
    try:
        raw = json.loads(prop.raw_json) if prop.raw_json else {}
    except Exception:
        raw = {}

    raw.setdefault("melissa", {})
    raw["melissa"]["LookupProperty"] = prop_payload
    if deeds_payload is not None:
        raw["melissa"]["LookupDeeds"] = deeds_payload
    prop.raw_json = json.dumps(raw)

    # 5) Normalize + promote some columns
    if recs:
        try:
            norm = normalize_property_record(recs[0], deeds_payload=deeds_payload)
            raw = json.loads(prop.raw_json or "{}")
            raw["ownership_mortgage"] = norm.get("ownership") or {}
            raw["2_classification"]   = norm.get("classification") or {}
            meta = raw.get("meta", {})
            meta.setdefault("sources", {}).update((norm.get("meta") or {}).get("sources", {}))
            meta.setdefault("as_of",   {}).update((norm.get("meta") or {}).get("as_of",   {}))
            raw["meta"] = meta
            prop.raw_json = json.dumps(raw)

            s = norm.get("structure") or {}
            if (prop.beds or 0) == 0 and s.get("beds"):           prop.beds = s["beds"]
            if (prop.baths or 0) == 0 and s.get("baths"):         prop.baths = s["baths"]
            if (prop.sqft or 0) == 0 and s.get("sqft"):           prop.sqft = s["sqft"]
            if (prop.year_built or 0) == 0 and s.get("year_built"): prop.year_built = s["year_built"]
        except Exception:
            current_app.logger.exception("Melissa normalize failed")

    db.session.commit()
    flash("Enriched from Melissa.", "success")
    return redirect(url_for('main.property_detail', property_id=prop.id))

@main.route('/learn/seller-financing')
def learn_seller_financing():
    return render_template('learn_seller_financing.html')

# ---------- Aliases ----------
@main.route('/lead_form', methods=['GET'])
def lead_form_alias():
    return redirect(url_for('main.lead_new_step1'))

@main.route("/comps", methods=["GET"])
def api_comps():
    a1    = request.values.get("address1") or request.values.get("address")
    city  = request.values.get("city")
    state = request.values.get("state")
    postal= request.values.get("postalcode") or request.values.get("zip")
    lat   = request.values.get("latitude")
    lon   = request.values.get("longitude")

    def _to_float(v):
        try: return float(v)
        except (TypeError, ValueError): return None

    # tighter defaults for comps
    try:
        radius = float(request.values.get("radius", 0.5) or 0.5)
    except (TypeError, ValueError):
        radius = 0.5

    lat_f, lon_f = _to_float(lat), _to_float(lon)

    try:
        if lat_f is not None and lon_f is not None:
            payload = attom_svc.sale_comps(
                lat=lat_f, lon=lon_f, radius_miles=radius,
                page_size=50, last_n_months=6
            )
        else:
            payload = attom_svc.sale_comps(
                address1=a1, city=city, state=state, postalcode=postal,
                radius_miles=radius, page_size=50, last_n_months=6
            )

        comps = attom_svc.extract_comps(payload, max_items=50)

        # Optional subject constraints from query
        subj_sqft = _to_float(request.values.get("subject_sqft"))
        try:
            subj_year = int(request.values.get("subject_year")) if request.values.get("subject_year") else None
        except Exception:
            subj_year = None
        subj_sub  = request.values.get("subject_subdivision") or None

        good = attom_svc.filter_comps_rules(
            comps,
            subject_sqft=subj_sqft,
            subject_year=subj_year,
            subject_subdivision=subj_sub,
            max_months=6,
            max_radius_miles=0.5,
            sqft_tolerance=0.15,
            year_tolerance=5,
            require_subdivision=bool(subj_sub)  # require if provided
        )

        return jsonify({"status": "ok", "comps": good})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 400


@main.route("/properties/<int:property_id>/enrich_attom", methods=["POST"])
def enrich_attom(property_id):
    prop = Property.query.get_or_404(property_id)

    # Key check
    try:
        _ = attom_svc._key()
    except Exception:
        flash("ATTOM_API_KEY not configured. Set it in .env or app config.", "warning")
        return redirect(url_for("main.property_detail", property_id=prop.id))

    # Load raw container
    try:
        raw = json.loads(prop.raw_json) if prop.raw_json else {}
    except Exception:
        raw = {}

    # Clean/split address (address-only; no lat/lon in params)
    raw_addr = (prop.full_address or prop.address or "").strip()
    raw_addr = re.sub(r"\s*,?\s*(USA|United States)$", "", raw_addr, flags=re.I)
    a1, city, state, postal = _split_us_address(raw_addr)

    # 1) Property detail (get coords + rich facts)
    try:
        detail_payload = attom_svc.property_detail(address1=a1, city=city, state=state, postalcode=postal)
    except Exception as e:
        detail_payload = {"_error": str(e)}

    # Try to harvest coords & basics from detail
    lat, lon = attom_svc.extract_detail_coords(detail_payload)
    basics   = attom_svc.extract_detail_basics(detail_payload) if isinstance(detail_payload, dict) else {}

    # Subject info for rules
    def _to_float(v):
        try: return float(v)
        except Exception: return None
    def _to_int(v):
        try: return int(v)
        except Exception: return None

    subject_sqft = _to_float(basics.get("sqft")) or _to_float(prop.sqft)
    subject_year = _to_int(basics.get("yearBuilt")) or _to_int(prop.year_built)

    subject_sub = None
    try:
        # Melissa: LookupProperty → Records[0].Legal.Subdivision
        subject_sub = (((raw.get("melissa") or {}).get("LookupProperty") or {})
                        .get("Records") or [])[0].get("Legal", {}).get("Subdivision")
    except Exception:
        subject_sub = None

    # 2) AVM + Rental AVM (ADDRESS ONLY)
    try:
        avm_payload = attom_svc.avm(address1=a1, city=city, state=state, postalcode=postal)
    except Exception as e:
        avm_payload = {"_error": str(e)}

    try:
        rent_payload = attom_svc.rental_avm(address1=a1, city=city, state=state, postalcode=postal)
    except Exception as e:
        rent_payload = {"_error": str(e)}

    # 3) Comps — prefer coordinates; fallback to address (tight defaults)
    try:
        # allow override via form, else use 0.5 mi
        try:
            radius = float(request.values.get("radius", 0.5) or 0.5)
        except Exception:
            radius = 0.5

        if lat not in (None, "", "null") and lon not in (None, "", "null"):
            comps_payload = attom_svc.sale_comps(
                lat=float(lat), lon=float(lon),
                radius_miles=radius, page_size=50, last_n_months=6
            )
        else:
            comps_payload = attom_svc.sale_comps(
                address1=a1, city=city, state=state, postalcode=postal,
                radius_miles=radius, page_size=50, last_n_months=6
            )
    except Exception as e:
        comps_payload = {"_error": str(e)}

    # Normalize comps, then apply rules
    comps = attom_svc.extract_comps(comps_payload, max_items=50)
    good_comps = attom_svc.filter_comps_rules(
        comps,
        subject_sqft=subject_sqft,
        subject_year=subject_year,
        subject_subdivision=subject_sub,
        max_months=6,
        max_radius_miles=0.5,
        sqft_tolerance=0.15,
        year_tolerance=5,
        require_subdivision=bool(subject_sub),  # require match if we know the subdivision
    )

    # 4) Schools (address-only)
    try:
        schools_payload = attom_svc.detail_with_schools(address1=a1, city=city, state=state, postalcode=postal)
    except Exception as e:
        schools_payload = {"_error": str(e)}

    # ----- Activity log entries (ATTOM) -----
    if isinstance(detail_payload, dict):
        _log(
            raw, source="ATTOM", event="property_detail",
            status=(detail_payload.get("status", {}) or {}).get("code", "ok"),
            note="property/detail", meta={"has_property": bool((detail_payload or {}).get("property"))},
        )
    if isinstance(avm_payload, dict):
        status_code = (avm_payload.get("status") or {}).get("code", "ok")
        try:
            avm_val = (((avm_payload.get("property") or [])[0].get("avm") or {}).get("amount") or {}).get("value")
        except Exception:
            avm_val = None
        _log(raw, source="ATTOM", event="avm", status=status_code,
             note=(f"avm value={avm_val}" if avm_val is not None else "avm"))
    if isinstance(rent_payload, dict):
        _log(raw, source="ATTOM", event="rental_avm",
             status=(rent_payload.get("status") or {}).get("code", "ok"),
             note="valuation/rentalavm")
    if isinstance(comps_payload, dict):
        code = (comps_payload.get("status") or {}).get("code", "ok")
        n    = len(comps_payload.get("property", []))
        _log(raw, source="ATTOM", event="sale_snapshot", status=code,
             note=f"{n} raw comps", meta={"radius": radius})
    if isinstance(schools_payload, dict):
        _log(raw, source="ATTOM", event="detail_with_schools",
             status=(schools_payload.get("status") or {}).get("code", "ok"),
             note="property/detailwithschools")

    # Save raw
    raw.setdefault("attom", {})
    raw["attom"] = {
        "detail": detail_payload,
        "avm": avm_payload,
        "rental_avm": rent_payload,
        "sale_snapshot": comps_payload,
        "detail_with_schools": schools_payload,
        "as_of": datetime.utcnow().strftime("%Y-%m-%d"),
    }

    # Light extracts for UI (store FILTERED comps)
    v, lo, hi, avm_asof, conf = attom_svc.extract_avm_numbers(avm_payload)
    rv, rlo, rhi, r_asof = attom_svc.extract_rental_avm_numbers(rent_payload)
    raw["attom_extract"] = {
        "avm": {"value": v, "low": lo, "high": hi, "as_of": avm_asof, "confidence": conf},
        "rental_avm": {"value": rv, "low": rlo, "high": rhi, "as_of": r_asof},
        "comps": good_comps,  # << filtered comps shown in UI
        "schools": attom_svc.extract_schools(schools_payload, max_items=5),
    }

    # Promote basics into Property if blank
    if basics:
        prop.full_address = basics.get("fullAddress") or prop.full_address
        if not prop.beds and basics.get("beds"):            prop.beds = basics["beds"]
        if not prop.baths and basics.get("baths"):          prop.baths = basics["baths"]
        if not prop.sqft and basics.get("sqft"):            prop.sqft = basics["sqft"]
        if not prop.year_built and basics.get("yearBuilt"): prop.year_built = basics["yearBuilt"]
        if not prop.lat and basics.get("lat"):
            try: prop.lat = float(basics["lat"])
            except Exception: pass
        if not prop.lng and basics.get("lng"):
            try: prop.lng = float(basics["lng"])
            except Exception: pass

    prop.raw_json = json.dumps(raw)
    db.session.add(prop)
    db.session.commit()
    flash("ATTOM data refreshed.", "success")
    return redirect(url_for("main.property_detail", property_id=property_id))
