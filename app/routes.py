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
import re as regex

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
from urllib.parse import quote



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



def _zillow_url_from_address(a1=None, city=None, state=None, postal=None):
    """
    Build a Zillow search URL for a given address.
    """
    parts = [p for p in [a1, city, state, postal] if p]
    if not parts:
        return None
    slug = "-".join(" ".join(parts).replace(",", "").split())
    return f"https://www.zillow.com/homes/{quote(slug)}/"


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
        m = regex.match(p, addr)
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

    # Build unified snapshot (existing helper)
    snapshot = build_snapshot_for_property(prop) or {}

    # Parse raw_json
    try:
        raw = json.loads(prop.raw_json) if prop.raw_json else {}
    except Exception:
        raw = {}

    # Estimated repairs (unchanged)
    lead_for_prop = (Lead.query.filter(Lead.address == prop.address)
                     .order_by(Lead.id.desc()).first())
    est_repairs = None
    if lead_for_prop:
        intake = lead_for_prop.intake
        if isinstance(intake, str):
            try:
                intake = json.loads(intake)
            except Exception:
                intake = {}
        if isinstance(intake, dict):
            est_repairs = intake.get("repairs_cost_est")

    # comps from ATTOM extract
    attx = (raw.get("attom_extract") or {})
    comps_list = attx.get("comps") or []
    ai_comps   = attx.get("comps_selected") or []

    def _addr_parts(c):
        loc = (c.get("location") or {}).get("address", {}) if isinstance(c, dict) else {}
        a1   = c.get("address1") or c.get("address") or loc.get("line")
        city = c.get("city")     or loc.get("city")
        st   = c.get("state")    or loc.get("state")
        zipc = c.get("postalcode") or c.get("postalCode") or loc.get("postalCode")
        return a1, city, st, zipc

    def _addr_line(a1, city, st, zipc):
        parts = []
        if a1: parts.append(a1)
        cs = ", ".join([p for p in [city, st] if p])
        if cs: parts.append(cs)
        s = " ".join(parts)
        return (f"{s} {zipc}".strip() if zipc else s)

    # add label + Zillow URL to BOTH lists
    for group in (comps_list, ai_comps):
        for c in group:
            a1, city, st, zipc = _addr_parts(c)
            c["_addr_line"] = _addr_line(a1, city, st, zipc)
            c["zillow_url"] = _zillow_url_from_address(a1, city, st, zipc)

    # pass ai_comps to the template
    return render_template(
        "property_detail.html",
        prop=prop,
        snapshot=snapshot,
        est_repairs=est_repairs,
        comps_list=comps_list,
        ai_comps=ai_comps,               # <-- keep this
        raw=raw,
        GOOGLE_MAPS_API_KEY=current_app.config.get("GOOGLE_MAPS_API_KEY", "")
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
    raw_addr_clean = regex.sub(r'\s*,?\s*(USA|United States)$', '', raw_addr, flags=regex.I)


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

@main.route("/properties/<int:property_id>/comps_rules", methods=["POST"], endpoint="save_comp_rules")
def save_comp_rules(property_id: int):
    """Persist comp-rule settings posted from the UI, then refresh comps."""
    prop = Property.query.get_or_404(property_id)

    def _to_float(v):
        try: return float(v) if v not in (None, "") else None
        except Exception: return None

    def _to_int(v):
        try: return int(float(v)) if v not in (None, "") else None
        except Exception: return None

    def _to_bool(v):
        if v is None: return False
        s = str(v).strip().lower()
        return s not in ("", "0", "false", "no", "off", "none")

    # ---- read form values ----
    # allow either "radius" or "max_radius_miles"
    radius_in = request.form.get("radius") or request.form.get("max_radius_miles")
    radius = _to_float(radius_in)

    max_months     = _to_int(request.form.get("max_months"))

    # UI sends a PERCENT (e.g., 15 for ±15%)
    pct = _to_float(request.form.get("sqft_tolerance_pct"))
    if pct is None:
        pct = _to_float(request.form.get("sqft_tolerance"))
    sqft_tolerance = (pct / 100.0) if pct is not None else None

    year_tolerance     = _to_int(request.form.get("year_tolerance"))
    require_subdivision= _to_bool(request.form.get("require_subdivision"))

    # ---- sane caps ----
    if radius is not None:
        radius = max(0.1, min(5.0, radius))
    if sqft_tolerance is not None:
        sqft_tolerance = max(0.01, min(1.0, sqft_tolerance))  # fraction, not percent
    if max_months is not None:
        max_months = max(1, min(36, max_months))
    if year_tolerance is not None:
        year_tolerance = max(0, min(50, year_tolerance))

    # optional subject overrides
    subject_sqft = _to_int(request.form.get("subject_sqft"))
    subject_year = _to_int(request.form.get("subject_year"))
    subject_sub  = (request.form.get("subject_subdivision") or "").strip() or None

    # save to raw
    try:
        raw = json.loads(prop.raw_json) if prop.raw_json else {}
    except Exception:
        raw = {}

    rules = {
        "radius": radius,
        "max_months": max_months,
        "sqft_tolerance": sqft_tolerance,  # fraction (0.15 = ±15%)
        "year_tolerance": year_tolerance,
        "require_subdivision": require_subdivision,
        "subject_sqft": subject_sqft,
        "subject_year": subject_year,
        "subject_subdivision": subject_sub,
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    raw["comp_rules"] = rules
    prop.raw_json = json.dumps(raw)
    db.session.add(prop)
    db.session.commit()

    # redirect into ATTOM refresh (GET)
    q = {}
    if radius is not None:         q["radius"] = radius
    if max_months is not None:     q["max_months"] = max_months
    if sqft_tolerance is not None: q["sqft_tolerance"] = sqft_tolerance
    if year_tolerance is not None: q["year_tolerance"] = year_tolerance
    if require_subdivision:        q["require_subdivision"] = "1"
    if subject_sqft is not None:   q["subject_sqft"] = subject_sqft
    if subject_year is not None:   q["subject_year"] = subject_year
    if subject_sub:                q["subject_subdivision"] = subject_sub

    flash("Comp rules saved.", "success")
    return redirect(url_for("main.enrich_attom", property_id=prop.id, **q))


@main.route("/properties/<int:property_id>/enrich_attom", methods=["GET", "POST"])
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

    # -------- FIX: build & clean the address --------
    raw_addr = (prop.full_address or prop.address or "").strip()
    raw_addr = regex.sub(r"\s*,?\s*(USA|United States)$", "", raw_addr, flags=regex.I)

    a1, city, state, postal = _split_us_address(raw_addr)

        # Saved rules (with fallbacks)
    rules = (raw.get("comp_rules") or {})
    def _to_float(v):
        try: return float(v)
        except Exception: return None
    def _to_int(v):
        try: return int(float(v))
        except Exception: return None

    # Percent in UI; store fraction internally
    sqft_tol = rules.get("sqft_tolerance")
    if sqft_tol is None:
        sqft_tol = 0.15
    # Cap to [0.01, 1.0] (1.0 == ±100%)
    sqft_tol = max(0.01, min(1.0, float(sqft_tol)))

    max_months = _to_int(rules.get("max_months")) or 6
    max_months = max(1, min(36, max_months))

    radius = _to_float(rules.get("radius"))
    if radius is None:
        radius = 0.5
    # Huge radii cause timeouts; cap to something reasonable
    radius = max(0.1, min(5.0, radius))

    year_tol = _to_int(rules.get("year_tolerance")) or 5
    year_tol = max(0, min(50, year_tol))

    require_subdivision = bool(rules.get("require_subdivision"))



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
        subject_sub = (((raw.get("melissa") or {}).get("LookupProperty") or {})
                        .get("Records") or [])[0].get("Legal", {}).get("Subdivision")
    except Exception:
        pass

    # 2) AVM + Rental AVM (ADDRESS ONLY)
    try:
        avm_payload = attom_svc.avm(address1=a1, city=city, state=state, postalcode=postal)
    except Exception as e:
        avm_payload = {"_error": str(e)}

    try:
        rent_payload = attom_svc.rental_avm(address1=a1, city=city, state=state, postalcode=postal)
    except Exception as e:
        rent_payload = {"_error": str(e)}

    # Determine the subject property kind so we compare apples to apples
    subject_kind = None
    try:
        m_records = (((raw.get("melissa") or {}).get("LookupProperty") or {}).get("Records") or [])
        if m_records:
            pu = (m_records[0].get("PropertyUseInfo") or {})
            subject_kind = pu.get("PropertyUseGroup") or pu.get("PropertyUse") or None
    except Exception:
        pass
    if not subject_kind:
        try:
            _summ = (((detail_payload or {}).get("property") or [{}])[0].get("summary") or {})
            subject_kind = _summ.get("propLandUse") or _summ.get("propertyType") or _summ.get("propclass")
        except Exception:
            pass

    # 3) Comps — prefer coords; RETRY with smaller search on failure/timeouts
    def _fetch_comps(r, m):
        if lat not in (None, "", "null") and lon not in (None, "", "null"):
            return attom_svc.sale_comps(lat=float(lat), lon=float(lon),
                                        radius_miles=r, page_size=50, last_n_months=m)
        return attom_svc.sale_comps(address1=a1, city=city, state=state, postalcode=postal,
                                    radius_miles=r, page_size=50, last_n_months=m)

    try:
        comps_payload = _fetch_comps(radius, max_months)
        # If ATTOM responds but with no properties, consider retrying once
        if not isinstance(comps_payload, dict) or not (comps_payload.get("property") or []):
            raise RuntimeError("empty comps")
    except Exception as e1:
        current_app.logger.warning("ATTOM sale_comps failed (%s). Retrying smaller...", e1)
        try:
            comps_payload = _fetch_comps(min(1.0, radius), min(12, max_months))
        except Exception as e2:
            comps_payload = {"_error": str(e2), "property": []}

    # ---- Backfill price / sale date / type from raw payload ----
        # ---- Extract comps & backfill price/date/type ----
    comps = attom_svc.extract_comps(comps_payload, max_items=50)

    def _addr_key(line):
        return regex.sub(r"\s+", " ", (line or "").strip().lower())

    def _key_from_fields(a1, city, st, zipc):
        parts = []
        if a1: parts.append(a1)
        if city or st: parts.append(", ".join([p for p in [city, st] if p]))
        if zipc: parts.append(zipc)
        return _addr_key(" ".join(parts))

    raw_index = {}
    for p in (comps_payload.get("property") or []):
        addr = p.get("address") or {}
        line = (addr.get("oneLine")
                or f"{addr.get('line1') or ''}, {addr.get('locality') or ''}, {addr.get('countrySubd') or ''} {addr.get('postal1') or addr.get('postalcode') or ''}")
        sale = (p.get("sale") or {})
        amt  = (sale.get("amount") or {})
        summary = p.get("summary") or {}
        raw_index[_addr_key(line)] = {
            "price": amt.get("saleamt"),
            "saleDate": sale.get("saleTransDate") or amt.get("salerecdate") or sale.get("salesearchdate"),
            "ptype": (summary.get("propertyType") or summary.get("proptype")
                      or summary.get("propclass") or summary.get("propLandUse")),
        }

    def _addr_key_for_comp(c):
        loc = (c.get("location") or {}).get("address", {}) if isinstance(c, dict) else {}
        a1c  = c.get("address1") or c.get("address") or loc.get("line")
        cc   = c.get("city") or loc.get("city")
        st   = c.get("state") or loc.get("state")
        zipc = c.get("postalcode") or c.get("postalCode") or loc.get("postalCode")
        return _key_from_fields(a1c, cc, st, zipc)

    for c in comps:
        if c.get("price") in (None, "", "—"):
            info = raw_index.get(_addr_key_for_comp(c)) or {}
            price = ((c.get("sale") or {}).get("amount") or {}).get("saleamt") \
                    or (c.get("amount") or {}).get("saleamt") \
                    or c.get("saleamt") or c.get("lastSalePrice") \
                    or info.get("price")
            try:
                if price is not None:
                    c["price"] = float(price)
            except Exception:
                pass
        if not c.get("saleDate"):
            info = raw_index.get(_addr_key_for_comp(c)) or {}
            if info.get("saleDate"):
                c["saleDate"] = info["saleDate"]
        if not any(k in c for k in ("propertyType","propclass","proptype","propLandUse")):
            info = raw_index.get(_addr_key_for_comp(c)) or {}
            if info.get("ptype"):
                c["propertyType"] = info["ptype"]

    # ---- type-normalization & filter by subject kind ----
    def _canon_kind(val):
        s = str(val or "").lower()
        if "single" in s or "sfr" in s: return "sfr"
        if "condo" in s: return "condo"
        if "town"  in s: return "townhouse"
        if "duplex" in s or "triplex" in s or "quad" in s or "multi" in s: return "multi"
        if "manufactured" in s or "mobile" in s: return "manufactured"
        return None

    subj_summary = {}
    try:
        subj_summary = ((detail_payload or {}).get("property") or [{}])[0].get("summary") or {}
    except Exception:
        pass
    subject_kind = _canon_kind(
        basics.get("propertyType") or basics.get("proptype") or basics.get("propclass")
        or basics.get("propLandUse") or subj_summary.get("propertyType")
        or subj_summary.get("proptype") or subj_summary.get("propclass") or subj_summary.get("propLandUse")
    )

    def _comp_kind(c):
        return _canon_kind(c.get("propertyType") or c.get("propclass")
                           or c.get("proptype") or c.get("propsubtype") or c.get("propLandUse"))

    if subject_kind:
        comps = [c for c in comps if (_comp_kind(c) in (None, subject_kind))]

    # ---- apply numeric/date/radius rules (use your saved rule values) ----
    good_comps = attom_svc.filter_comps_rules(
        comps,
        subject_sqft=subject_sqft,
        subject_year=subject_year,
        subject_subdivision=subject_sub,
        max_months=max_months,
        max_radius_miles=radius,
        sqft_tolerance=sqft_tol,         # fraction
        year_tolerance=year_tol,
        require_subdivision=require_subdivision,
    )

    # 4) Schools (address-only)
    try:
        schools_payload = attom_svc.detail_with_schools(address1=a1, city=city, state=state, postalcode=postal)
    except Exception as e:
        schools_payload = {"_error": str(e)}

    # ----- Activity log entries (ATTOM) -----
    if isinstance(detail_payload, dict):
        _log(raw, source="ATTOM", event="property_detail",
             status=(detail_payload.get("status", {}) or {}).get("code", "ok"),
             note="property/detail", meta={"has_property": bool((detail_payload or {}).get("property"))})
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

    # ----- Save raw (preserve previous snapshot on failure) -----
    raw.setdefault("attom", {})
    prev_snapshot = (raw.get("attom") or {}).get("sale_snapshot")

    raw["attom"] = {
        "detail": detail_payload,
        "avm": avm_payload,
        "rental_avm": rent_payload,
        "sale_snapshot": (
            comps_payload
            if (isinstance(comps_payload, dict) and (comps_payload.get("property") or []))
            else (prev_snapshot or comps_payload)
        ),
        "detail_with_schools": schools_payload,
        "as_of": datetime.utcnow().strftime("%Y-%m-%d"),
    }



    # Light extracts for UI (store FILTERED comps)
    v, lo, hi, avm_asof, conf = attom_svc.extract_avm_numbers(avm_payload)
    rv, rlo, rhi, r_asof = attom_svc.extract_rental_avm_numbers(rent_payload)
    raw["attom_extract"] = {
        "avm": {"value": v, "low": lo, "high": hi, "as_of": avm_asof, "confidence": conf},
        "rental_avm": {"value": rv, "low": rlo, "high": rhi, "as_of": r_asof},
        "comps": good_comps,
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

# --- AI/Heuristic comps selection ---
@main.route("/properties/<int:property_id>/comps_ai_select", methods=["POST"])
def comps_ai_select(property_id):
    # Prefer AI helper if available; otherwise use a built-in heuristic
    try:
        from .services.ai import choose_best_comps_with_ai, score_comps_heuristic
        have_ai = True
    except Exception:
        have_ai = False
        choose_best_comps_with_ai = None

        # lightweight heuristic fallback (distance, recency, sqft similarity)
        from datetime import datetime
        def _to_float(x):
            try: return float(x)
            except Exception: return None
        def _days_ago(dstr):
            try:
                d = datetime.fromisoformat(str(dstr)[:10]).date()
                return (datetime.utcnow().date() - d).days
            except Exception:
                return None
        def score_comps_heuristic(subject, comps):
            s_sqft = _to_float(subject.get("sqft"))
            out = []
            for c in comps:
                dist = _to_float(c.get("distance")) or 9.9
                days = _days_ago(c.get("saleDate")) or 9999
                c_sqft = _to_float(c.get("sqft"))
                sqft_pen = abs((c_sqft - s_sqft)/s_sqft) if (s_sqft and c_sqft) else 0.5
                # smaller is better: combine with weights
                s = (dist*2.0) + (days/90.0) + (sqft_pen*3.0)
                out.append((s, c))
            out.sort(key=lambda x: x[0])
            return [c for _, c in out]

    prop = Property.query.get_or_404(property_id)

    # Load raw
    try:
        raw = json.loads(prop.raw_json) if prop.raw_json else {}
    except Exception:
        raw = {}

    attx = (raw.get("attom_extract") or {})
    candidates = attx.get("comps") or []
    if not candidates:
        snap = (raw.get("attom") or {}).get("sale_snapshot") or {}
        candidates = attom_svc.extract_comps(snap, max_items=100)

    # Type-aware filtering for AI candidates
    def _canon_kind(val):
        s = str(val or "").lower()
        if "single" in s or "sfr" in s: return "sfr"
        if "condo" in s: return "condo"
        if "town"  in s: return "townhouse"
        if "duplex" in s or "triplex" in s or "quad" in s or "multi" in s: return "multi"
        if "manufactured" in s or "mobile" in s: return "manufactured"
        return None

    def _comp_kind(c):
        return _canon_kind(c.get("propertyType") or c.get("propclass")
                           or c.get("proptype") or c.get("propsubtype") or c.get("propLandUse"))

    # Subject kind from ATTOM detail if present
    try:
        d = ((raw.get("attom") or {}).get("detail") or {}).get("property") or []
        subj_summary = (d[0] or {}).get("summary") if d else {}
    except Exception:
        subj_summary = {}
    subject_kind = _canon_kind(
        subj_summary.get("propertyType") or subj_summary.get("proptype")
        or subj_summary.get("propclass") or subj_summary.get("propLandUse")
    )

    if subject_kind:
        candidates = [c for c in candidates if (_comp_kind(c) in (None, subject_kind))]


    subj = {
        "address": prop.full_address or prop.address,
        "beds": prop.beds,
        "baths": prop.baths,
        "sqft": prop.sqft,
        "yearBuilt": prop.year_built,
        "lat": prop.lat, "lng": prop.lng,
    }

    try:
        top_k = int(request.form.get("k") or 6)
    except Exception:
        top_k = 6

    picked, notes = [], ""
    if have_ai and choose_best_comps_with_ai:
        try:
            picked, notes = choose_best_comps_with_ai(subj, candidates, k=top_k)
        except Exception as e:
            current_app.logger.exception("AI comps selection failed")
            notes = f"AI selection failed: {e}. Falling back to heuristic."

    if not picked:
        picked = score_comps_heuristic(subj, candidates)[:top_k]
        if not notes:
            notes = "Heuristic selection."

    attx["comps_selected"] = picked
    attx["comps_selected_notes"] = notes
    raw["attom_extract"] = attx

    prop.raw_json = json.dumps(raw)
    db.session.add(prop)
    db.session.commit()

    flash(f"Selected {len(picked)} comps.", "success")
    current_app.logger.info("comps_ai_select: relative-imports OK")
    return redirect(url_for("main.property_detail", property_id=property_id))

# --- Save manual edits (and locks) ---
@main.route("/properties/<int:property_id>/update", methods=["POST"], endpoint="property_update")
def property_update(property_id):
    from flask import request, redirect, url_for, flash
    import json

    prop = Property.query.get_or_404(property_id)

    # load raw json safely
    try:
        raw = json.loads(prop.raw_json) if prop.raw_json else {}
    except Exception:
        raw = {}

    locks = raw.get("locks") or {}
    if not isinstance(locks, dict):
        locks = {}
    ux = raw.get("user_overrides") or {}

    def _to_int(v):
        try:
            return int(v) if v not in (None, "") else None
        except Exception:
            return None

    def _to_float(v):
        try:
            return float(v) if v not in (None, "") else None
        except Exception:
            return None

    # Editable core columns from the form
    full_address    = request.form.get("full_address")
    beds            = request.form.get("beds")
    baths           = request.form.get("baths")
    sqft            = request.form.get("sqft")
    lot_size        = request.form.get("lot_size")
    year_built      = request.form.get("year_built")
    lat             = request.form.get("lat")
    lng             = request.form.get("lng")
    school_district = request.form.get("school_district")

    if full_address is not None:     prop.full_address    = full_address or prop.full_address
    if beds is not None:             prop.beds            = _to_int(beds)
    if baths is not None:            prop.baths           = _to_float(baths)
    if sqft is not None:             prop.sqft            = _to_int(sqft)
    if lot_size is not None:         prop.lot_size        = _to_int(lot_size)
    if year_built is not None:       prop.year_built      = _to_int(year_built)
    if lat is not None:              prop.lat             = _to_float(lat)
    if lng is not None:              prop.lng             = _to_float(lng)
    if school_district is not None:  prop.school_district = (school_district or None)

    # Extra overrides not in the model but stored in raw
    for k in ("hoa", "subdivision", "notes"):
        if k in request.form:
            ux[k] = request.form.get(k) or None
    raw["user_overrides"] = ux

    # Locks: look for checkboxes named lock_<field>
    for f in ("full_address","beds","baths","sqft","lot_size","year_built","lat","lng","school_district","hoa","subdivision"):
        locks[f] = bool(request.form.get(f"lock_{f}"))
    raw["locks"] = locks

    # persist
    prop.raw_json = json.dumps(raw)
    db.session.add(prop)
    db.session.commit()

    flash("Saved changes and locks.", "success")
    return redirect(url_for("main.property_detail", property_id=prop.id))


