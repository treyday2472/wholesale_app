# --- imports ---
from __future__ import annotations
import os
import json
from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, current_app, session, jsonify
)

from .helpers.lead_helpers import ensure_property_and_initial_offer

from datetime import datetime
from math import radians, sin, cos, asin, sqrt
from .services.ai import suggest_arv



import re as regex

from werkzeug.utils import secure_filename
from wtforms.validators import Optional  # relaxing validators after 3 tries

from .services.auto_offer import auto_enrich_and_offer_for_lead

from . import db
from .models import Lead, Buyer, Property
from .forms import (
    LeadStep1Form, UpdateStatusForm,
    BuyerStep1Form, BuyerStep2Form, PropertyForm, LeadStep3MoreForm, LeadStep2CoreForm
)

#from services.zillow_fetch import search_recently_sold
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

def _update_pipeline_for_motivation(prop: Property, motivation_score: int):
    prop.motivation_score = motivation_score

    # 1–4: cold or nurture-only
    if motivation_score <= 4:
        # keep evaluation_stage as-is, no MLS review
        prop.needs_mls_review = False

    # 5–6: warm, send preliminary offer (Stage 2)
    elif 5 <= motivation_score <= 6:
        # your logic should have already run Stage 2 ARV (Zillow + Melissa + public)
        prop.evaluation_stage = max(prop.evaluation_stage or 1, 2)
        prop.needs_mls_review = False
        # here you'd also queue a “send soft offer” task for your comms AI

    # 7–10: hot, needs real MLS comps
    else:
        prop.evaluation_stage = max(prop.evaluation_stage or 1, 2)  # ensure at least Stage 2 ran
        prop.needs_mls_review = True

    db.session.add(prop)
    db.session.commit()




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

def select_comps_for_arv(prop, raw, comps_list, ai_comps):
    """
    Decide which comps to feed into suggest_arv().

    Priority:
      1) If evaluation_stage == 3 and MLS comps exist → use MLS comps.
      2) Else if AI-selected comps exist → use ai_comps.
      3) Else → fallback to the baseline comps_list (Zillow / default).
    """
    ev_stage = getattr(prop, "evaluation_stage", None) or 0

    base_comps = comps_list or []
    ai_list = ai_comps or []

    # Future-proof: MLS comps will be stored on the raw blob
    # under "mls_comps" (list of comp dicts/rows).
    mls_list = []
    if raw:
        mls_list = raw.get("mls_comps") or []

    # 1) If MLS review is done, prefer MLS comps
    if ev_stage >= 3 and mls_list:
        return mls_list

    # 2) Otherwise, if we have AI-selected comps, use those
    if ai_list:
        return ai_list

    # 3) Fallback to the base Zillow/default comps
    return base_comps


@main.route('/')
def home():
    return redirect(url_for('main.leads_list'))

# ---------------- Step 1 ----------------
@main.route('/lead_step1', methods=['GET', 'POST'])
def lead_new_step1():
    form = LeadStep1Form()
    if form.validate_on_submit():

        # 1) Create the Property stub from the Step 1 address
        address = (form.address.data or "").strip()
        full_address = address  # tweak if you have city/state/zip fields

        prop = Property(
            address=address,
            full_address =full_address,
            source ="LeadStep1Form"
        )
        db.session.add(prop)
        db.session.flush()

        lead = Lead(
            seller_first_name=(form.seller_first_name.data or "").strip() or None,
            seller_last_name=(form.seller_last_name.data or "").strip() or None,
            phone=(form.phone.data or "").strip() or None,
            email=(form.email.data or "").strip() or None,
            address=address,
            lead_source="Web Form",
            property=prop,  # this sets lead.property_id via relationship
        )

        db.session.add(lead)
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
        
        return redirect(url_for('main.lead_new_step2', lead_id=lead.id))
    return render_template(
        'lead_step1.html',
        form=form,
        GOOGLE_MAPS_API_KEY=current_app.config.get('GOOGLE_MAPS_API_KEY', '')
        
    )




# ---------------- Step 2 (CORE ONLY: 5 fields) ----------------
@main.route("/leads/new/step2/<int:lead_id>", methods=["GET", "POST"])
def lead_new_step2(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    form = LeadStep2CoreForm(obj=lead)  # prefill from lead where it overlaps

    if form.validate_on_submit():
        # Save core motivation / numbers
        lead.condition          = form.condition.data
        lead.why_sell           = form.why_sell.data
        lead.timeline          = form.timeline.data
        lead.occupancy_status   = form.occupancy_status.data
        lead.listed_with_realtor = form.listed_with_realtor.data
        lead.property_type      = form.property_type.data
        # you can keep updating lead_source here or only Step1, your call
        if hasattr(form, "lead_source"):
            lead.lead_source = form.lead_source.data or lead.lead_source

        # Merge Step 2 answers into intake JSON
        intake = lead.intake or {}
        if not isinstance(intake, dict):
            intake = {}

        intake.update({
            "condition": lead.condition,
            "why_sell": lead.why_sell,
            "timeline": lead.timeline,
            "asking_price": lead.asking_price,
            "occupancy_status": lead.occupancy_status,
            "listed_with_realtor": lead.listed_with_realtor,
            "property_type": lead.property_type,
        })
        lead.intake = intake

        db.session.commit()
        return redirect(url_for("main.lead_new_step3", lead_id=lead.id))

    return render_template("lead_step2.html", form=form, lead=lead)


# ---------------- Step 3 (MORE: rest of fields + uploads) ----------------
@main.route("/leads/new/step3/<int:lead_id>", methods=["GET", "POST"])
def lead_new_step3(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    form = LeadStep3MoreForm()

    if form.validate_on_submit():
        # ---- save the rest of the fields (everything beyond the core 5) ----
        lead.repairs_needed           = form.repairs_needed.data or None
        lead.repairs_cost_est         = form.repairs_cost_est.data or None
        lead.worth_estimate           = form.worth_estimate.data or None

        lead.behind_on_payments       = form.behind_on_payments.data or None
        lead.behind_amount            = form.behind_amount.data or None
        lead.loan_balance             = form.loan_balance.data or None
        lead.monthly_payment          = form.monthly_payment.data or None
        lead.interest_rate            = form.interest_rate.data or None

        lead.will_sell_for_amount_owed = form.will_sell_for_amount_owed.data or None
        lead.how_much_owed             = form.how_much_owed.data or None

        lead.in_bankruptcy            = form.in_bankruptcy.data or None
        lead.lowest_amount            = form.lowest_amount.data or None
        lead.flexible_price           = form.flexible_price.data or None
        lead.seller_finance_interest  = form.seller_finance_interest.data or None

        lead.title_others             = (form.title_others.data or "").strip() or None
        lead.title_others_names       = (form.title_others_names.data or "").strip() or None
        lead.title_others_willing     = form.title_others_willing.data or None
        lead.how_hear_about_us        = form.how_hear_about_us.data or None
        lead.how_hear_other           = (form.how_hear_other.data or "").strip() or None
        lead.notes                    = (form.notes.data or "").strip() or None

        # ---- file uploads (photos/attachments) ----
        upload_dir = current_app.config.get(
            "UPLOAD_FOLDER",
            os.path.join(current_app.static_folder, "uploads")
        )
        os.makedirs(upload_dir, exist_ok=True)
        saved_files = []

        files = []
        if "attachments" in request.files:
            files.extend(request.files.getlist("attachments"))
        if "photos" in request.files:
            files.extend(request.files.getlist("photos"))

        for file in files:
            if not file or not getattr(file, "filename", ""):
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
            lead.image_files = ",".join(
                filter(None, [(lead.image_files or ""), ",".join(saved_files)])
            )

        # ---- Merge Step 3 answers into intake JSON ----
        raw_intake = lead.intake or {}
        if isinstance(raw_intake, dict):
            intake = dict(raw_intake)  # copy
        elif isinstance(raw_intake, str):
            try:
                import json
                intake = json.loads(raw_intake) or {}
            except Exception:
                intake = {}
        else:
            intake = {}

        # helper to keep JSON-safe types
        def safe_int(v):
            return int(v) if v not in (None, "") else None

        def safe_float(v):
            return float(v) if v not in (None, "") else None

        intake.update({
            # step 3 repairs / value
            "repairs_needed": lead.repairs_needed,
            "repairs_cost_est": safe_int(form.repairs_cost_est.data),

            "worth_estimate": lead.worth_estimate,

            # step 3 financial distress
            "behind_on_payments": lead.behind_on_payments,
            "behind_amount": safe_int(form.behind_amount.data),
            "loan_balance": safe_int(form.loan_balance.data),
            "monthly_payment": safe_int(form.monthly_payment.data),
            "interest_rate": lead.interest_rate,

            # step 3 deal terms
            "will_sell_for_amount_owed": lead.will_sell_for_amount_owed,
            "how_much_owed": safe_float(form.how_much_owed.data),
            "in_bankruptcy": lead.in_bankruptcy,
            "lowest_amount": safe_int(form.lowest_amount.data),
            "flexible_price": lead.flexible_price,
            "seller_finance_interest": lead.seller_finance_interest,

            # title & marketing
            "title_others": lead.title_others,
            "title_others_names": lead.title_others_names,
            "title_others_willing": lead.title_others_willing,
            "how_hear_about_us": lead.how_hear_about_us,
            "how_hear_other": lead.how_hear_other,
        })

        lead.intake = intake

        offer = ensure_property_and_initial_offer
        db.session.commit()
        return redirect(url_for("main.lead_detail", lead_id=lead.id))

    # GET or failed validation
    return render_template("lead_step3.html", form=form, lead=lead)



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

    # Prefill status form from lead
    form = UpdateStatusForm(lead_status=lead.lead_status)

    # --- Normalize intake into a dict ---
    raw_intake = lead.intake or {}
    if isinstance(raw_intake, dict):
        intake = raw_intake
    elif isinstance(raw_intake, str):
        try:
            import json
            intake = json.loads(raw_intake) or {}
        except Exception:
            intake = {}
    else:
        intake = {}

    # --- Get linked property (relationship first, fallback by address) ---
    prop = getattr(lead, "property", None)
    if prop is None and lead.address:
        prop = (
            Property.query
            .filter_by(address=lead.address)
            .order_by(Property.id.desc())
            .first()
        )

    # --- Handle status update POST ---
    if form.validate_on_submit():
        lead.lead_status = form.lead_status.data
        db.session.commit()
        flash("Lead status updated.", "success")
        return redirect(url_for('main.lead_detail', lead_id=lead.id))

    # --- Parse images list safely ---
    if lead.image_files:
        images = [f.strip() for f in lead.image_files.split(",") if f.strip()]
    else:
        images = []

    # --- Render detail page ---
    from flask import current_app  # if not already imported at top
    SF_ENABLED = current_app.config.get("SF_ENABLED", False)
    SF_INSTANCE_URL = current_app.config.get("SF_INSTANCE_URL", "")

    return render_template(
        'lead_detail.html',
        lead=lead,
        form=form,
        images=images,
        intake=intake,
        prop=prop,
        SF_ENABLED=SF_ENABLED,
        SF_INSTANCE_URL=SF_INSTANCE_URL,
    )

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
    return render_template('properties_list.html', props=props, q=q,)

@main.route("/properties/<int:property_id>/needs-mls-review", methods=["POST"])
def mark_needs_mls_review(property_id):
    prop = Property.query.get_or_404(property_id)

    prop.needs_mls_review = True

    if not prop.evaluation_stage or prop.evaluation_stage < 2:
        prop.evaluation_stage = 2

    db.session.commit()
    flash("Property flagged as needing MLS review.", "info")
    return redirect(url_for("main.property_detail", property_id=property_id))


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

    # unified snapshot (kept)
    snapshot = build_snapshot_for_property(prop) or {}

    # parse raw_json safely
    try:
        raw = json.loads(prop.raw_json) if prop.raw_json else {}
    except Exception:
        raw = {}

    # ---- repairs from latest Lead with same address (unchanged) ----
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

    # ---- Zillow block pulled from raw_json ----
    raw_z = (raw.get("zillow") or {})

    # ---- comps come only from any precomputed extract in raw (keep names if you already store them) ----
    comps_list = (raw.get("comps") or [])
    ai_comps   = (raw.get("comps_selected") or [])

    # Helper: zillow url by address
    def _zillow_url_from_address(a1, city, st, zipc):
        if not a1:
            return None
        # relative is fine; your template opens in new tab
        parts = [a1.replace(' ', '-')]
        if city: parts.append(city.replace(' ', '-'))
        if st:   parts.append(st)
        if zipc: parts.append(f"{zipc}")
        return "/homedetails/" + "-".join(parts) + "/"

    # address parts for comps enrichment
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

    # add label + Zillow URL to BOTH comp lists
    for group in (comps_list, ai_comps):
        for c in group:
            a1, city, st, zipc = _addr_parts(c)
            c["_addr_line"] = _addr_line(a1, city, st, zipc)
            c["zillow_url"] = _zillow_url_from_address(a1, city, st, zipc)

    comp_source = ai_comps if ai_comps else comps_list

    subject = {
        "address":   prop.full_address or prop.address,
        "beds":      prop.beds,
        "baths":     prop.baths,
        "sqft":      prop.sqft,
        "yearBuilt": prop.year_built,
        "lat":       prop.lat,
        "lng":       prop.lng,
    }

    # simple AVM bundle (ATTOM removed)
    def _num(x):
        try: return float(x)
        except: return None

    avm_bundle = {
        "zestimate": _num(raw_z.get("zestimate")),
        "melissa":   None,   # leave hook if you still load Melissa elsewhere
        "attom":     None,   # removed
    }

    arv_pack, arv_notes = suggest_arv(subject, comp_source, k=6, avm=avm_bundle)

    return render_template(
        "property_detail.html",
        prop=prop,
        property=prop,
        snapshot=snapshot,
        est_repairs=est_repairs,
        comps_list=comps_list,
        ai_comps=ai_comps,
        raw=raw,
        arv_pack=arv_pack,
        arv_notes=arv_notes,
        GOOGLE_MAPS_API_KEY=current_app.config.get("GOOGLE_MAPS_API_KEY", ""),
        lead_for_prop=lead_for_prop,
    )

@main.route('/properties/<int:property_id>/delete', methods=['POST'])
def delete_property(property_id):
    prop = Property.query.get_or_404(property_id)
    db.session.delete(prop)
    db.session.commit()
    flash("Property deleted.", "info")
    return redirect(url_for('main.properties_list'))

@main.route('/properties/<int:property_id>/refresh', methods=['GET','POST'], endpoint='property_refresh')
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
            prop.beds            = facts.get("bedrooms")    or prop.beds
            prop.baths           = facts.get("bathrooms")   or prop.baths
            prop.sqft            = facts.get("livingArea")  or prop.sqft
            prop.lot_size        = facts.get("lotSize")     or prop.lot_size
            prop.year_built      = facts.get("yearBuilt")   or prop.year_built
            prop.school_district = facts.get("schoolDistrict") or prop.school_district
            if not prop.lat and facts.get("lat"): prop.lat = facts["lat"]
            if not prop.lng and facts.get("lng"): prop.lng = facts["lng"]

            # 3) Pull zestimate/rent zestimate as “basics”
            basics = zillow_basics(prop.full_address or prop.address, rapid_key, details_host)

            home = (raw_details or {}).get("home") or {}
            sale_status = (home.get("homeStatus")
                           or home.get("homeStatusLabel")
                           or home.get("statusType") or "")
            sale_status_u = str(sale_status).upper()
            for_sale = bool(home.get("isForSale")) or sale_status_u in {
                "FOR_SALE", "PENDING", "COMING_SOON", "NEW"
            }
            list_price = (home.get("price")
                          or home.get("unformattedPrice")
                          or home.get("listPrice"))

            # update raw (always set/update, regardless of JSON load success)
            try:
                raw = json.loads(prop.raw_json) if prop.raw_json else {}
            except Exception:
                raw = {}

            raw.setdefault("zillow", {})
            if basics:
                # merge the basics dict (includes .raw.home and fields)
                raw["zillow"].update(basics)

            raw["zillow"].update({
                "for_sale": bool(for_sale),
                "sale_status": (sale_status or None),
                "list_price": list_price,
            })

            prop.raw_json = json.dumps(raw)
            
            


            # update snapshot details → hero photo logic uses this
            snapshot = prop.snapshot or {}
            snapshot.setdefault("details", {})
            snapshot["details"]["raw"] = (raw_details or {}).get("home", {})
            prop.snapshot = snapshot

        db.session.commit()
        flash("Zillow data refreshed.", "success")
    except Exception as e:
        current_app.logger.exception("Zillow refresh failed")
        flash(f"Zillow refresh failed: {e}", "danger")

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
        prop.raw_json["zillow"]
        
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

    def _hav_miles(lat1, lon1, lat2, lon2):
        R = 3958.7613  # earth radius (mi)
        φ1, φ2 = radians(lat1), radians(lat2)
        dφ = radians(lat2 - lat1)
        dλ = radians(lon2 - lon1)
        a = sin(dφ/2)**2 + cos(φ1) * cos(φ2) * sin(dλ/2)**2
        return 2 * R * asin(sqrt(a))

    def _clatlon(c):
        loc = (c.get("location") or {})
        # try several common shapes
        latc = (c.get("lat") or c.get("latitude") or loc.get("lat") or loc.get("latitude"))
        lonc = (c.get("lng") or c.get("lon") or c.get("longitude") or loc.get("lng") or loc.get("lon") or loc.get("longitude"))
        try:
            return float(latc), float(lonc)
        except Exception:
            return None, None

    try:
        slat = float(lat) if lat not in (None, "", "null") else None
        slon = float(lon) if lon not in (None, "", "null") else None
    except Exception:
        slat = slon = None

    if slat is not None and slon is not None:
        for c in comps:  # 'comps' is the list from extract_comps(...)
            if c.get("distance") in (None, "", 0):
                clat, clon = _clatlon(c)
                if clat is not None and clon is not None:
                    c["distance"] = round(_hav_miles(slat, slon, clat, clon), 3)

    # also accept 'mi' from an AI scorer as a fallback name
    for c in comps:
        if c.get("distance") in (None, "", 0) and c.get("mi") not in (None, "", 0):
            try:
                c["distance"] = float(c["mi"])
            except Exception:
                pass

    # ---- type-normalization & filter by subject kind ----
    def _canon_kind(val):
        s = str(val or "").lower()
        if "single" in s or "sfr" in s: return "sfr"
        if "condo" in s: return "condo"
        if "town"  in s: return "townhouse"
        if "duplex" in s: return "duplex"
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
    sqft_tolerance=sqft_tol,
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

@main.route("/properties/<int:property_id>/comps_ai_select", methods=["POST"])
def comps_ai_select(property_id):
    from .services.ai import choose_best_comps_with_ai, score_comps_heuristic
    from math import radians, sin, cos, asin, sqrt

    prop = Property.query.get_or_404(property_id)

    # Load raw JSON safely
    try:
        raw = json.loads(prop.raw_json or "{}")
    except Exception:
        raw = {}

    # --- helper: distance in miles (rough haversine) ---
    def _dist_miles(lat1, lng1, lat2, lng2):
        try:
            lat1 = float(lat1); lng1 = float(lng1)
            lat2 = float(lat2); lng2 = float(lng2)
        except (TypeError, ValueError):
            return None
        R = 3958.8
        dlat = radians(lat2 - lat1)
        dlon = radians(lng2 - lng1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        c = 2 * asin((a ** 0.5))
        return R * c

    # --- helper: build comps from Zillow "nearbyHomes" if needed ---
    def _build_comps_from_zillow(zraw, subj_lat, subj_lng):
        comps = []
        nearby = zraw.get("nearbyHomes") or []
        for h in nearby:
            addr = h.get("address") or {}
            a1   = addr.get("streetAddress")
            city = addr.get("city")
            st   = addr.get("state")
            zipc = addr.get("zipcode")

            # simple address line for UI
            parts = []
            if a1: parts.append(a1)
            cs = ", ".join([p for p in (city, st) if p])
            if cs: parts.append(cs)
            addr_line = " ".join(parts)
            if zipc:
                addr_line = f"{addr_line} {zipc}".strip()

            lat = h.get("latitude")
            lng = h.get("longitude")
            dist = _dist_miles(subj_lat, subj_lng, lat, lng) if (subj_lat and subj_lng and lat and lng) else None

            comps.append({
                # location
                "address": addr_line,
                "address1": a1,
                "city": city,
                "state": st,
                "postalcode": zipc,

                # core valuation fields
                "price": h.get("price"),
                "sqft": h.get("livingAreaValue") or h.get("livingArea"),
                "beds": h.get("bedrooms"),
                "baths": h.get("bathrooms"),
                "yearBuilt": h.get("yearBuilt"),

                # comparability helpers
                "distance": dist,
                "saleDate": None,  # Zillow nearbyHomes here are mostly Zestimates / current values
                "propertyType": h.get("homeType") or h.get("propertyTypeDimension"),
                "zpid": h.get("zpid"),
            })
        return comps

    # 1) Try to load any existing comps we already saved
    candidates = (raw.get("comps") or
                  raw.get("zillow_comps") or
                  raw.get("bridge_comps") or [])

    # 2) If there are none, try to auto-build from Zillow details
    if not candidates:
        zraw = raw.get("zillow") or {}
        subj_lat = prop.lat or zraw.get("latitude")
        subj_lng = prop.lng or zraw.get("longitude")

        if zraw:
            built = _build_comps_from_zillow(zraw, subj_lat, subj_lng)
            if built:
                candidates = built
                raw["comps"] = built   # <- now property_detail will see these too

    # 3) Still nothing? Then we really don't have comps
    if not candidates:
        flash("No comps available from Zillow for this property yet. You may need to run a full Zillow eval or add comps manually.", "warning")
        prop.raw_json = json.dumps(raw)
        db.session.add(prop)
        db.session.commit()
        return redirect(url_for("main.property_detail", property_id=property_id))

    # Subject snapshot from your Property model
    subj = {
        "address":   prop.full_address or prop.address,
        "beds":      prop.beds,
        "baths":     prop.baths,
        "sqft":      prop.sqft,
        "yearBuilt": prop.year_built,
        "lat":       prop.lat,
        "lng":       prop.lng,
    }

    # top K comps
    try:
        top_k = int(request.form.get("k") or 6)
    except Exception:
        top_k = 6

    picked, notes = [], ""
    try:
        picked, notes = choose_best_comps_with_ai(subj, candidates, k=top_k)
    except Exception as e:
        current_app.logger.exception("AI comps selection failed; falling back to heuristic")
        picked = score_comps_heuristic(subj, candidates)[:top_k]
        notes = f"AI selection failed: {e}. Heuristic selection applied."

    # Persist AI-selected comps & notes at TOP LEVEL (what property_detail expects)
    raw["comps_selected"] = picked
    raw["comps_selected_notes"] = notes

    prop.raw_json = json.dumps(raw)
    db.session.add(prop)
    db.session.commit()

    flash(f"Selected {len(picked)} comps.", "success")
    current_app.logger.info("comps_ai_select completed")
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

@main.route("/properties/<int:property_id>/mls_comps", methods=["GET", "POST"])
def mls_comps(property_id):
    prop = Property.query.get_or_404(property_id)

    # Load existing raw_json
    try:
        raw = json.loads(prop.raw_json or "{}")
    except Exception:
        raw = {}

    mls_comps = raw.get("mls_comps") or []

    if request.method == "POST":
        # Grab one comp from the form
        addr = request.form.get("comp_address") or ""
        price = request.form.get("sale_price") or ""
        sale_date = request.form.get("sale_date") or ""
        sqft = request.form.get("sqft") or ""
        beds = request.form.get("beds") or ""
        baths = request.form.get("baths") or ""
        year_built = request.form.get("year_built") or ""
        lot_size = request.form.get("lot_size") or ""
        distance = request.form.get("distance") or ""
        condition_notes = request.form.get("condition_notes") or ""
        photo_url = request.form.get("photo_url") or ""
        source = request.form.get("mls_source") or "PropStream"

        if not addr or not price or not sale_date:
            flash("Comp address, sale price, and sale date are required.", "warning")
        else:
            comp = {
                "address": addr,
                "price": float(price) if price else None,
                "saleDate": sale_date,
                "sqft": float(sqft) if sqft else None,
                "beds": int(beds) if beds else None,
                "baths": float(baths) if baths else None,
                "yearBuilt": int(year_built) if year_built else None,
                "lotSize": float(lot_size) if lot_size else None,
                "distance": float(distance) if distance else None,
                "conditionNotes": condition_notes,
                "photoUrl": photo_url,
                "source": source,
            }
            mls_comps.append(comp)
            raw["mls_comps"] = mls_comps
            # this is now ready for Stage 3 once VA is done
            prop.raw_json = json.dumps(raw)
            db.session.add(prop)
            db.session.commit()
            flash("MLS comp added.", "success")
            return redirect(url_for("main.mls_comps", property_id=property_id))

    return render_template(
        "mls_comps.html",
        prop=prop,
        mls_comps=mls_comps,
    )

@main.route("/properties/<int:property_id>/mls_comps_finalize", methods=["POST"])
def mls_comps_finalize(property_id):
    from .services.ai import choose_best_comps_with_ai, score_comps_heuristic
    from datetime import datetime

    prop = Property.query.get_or_404(property_id)

    try:
        raw = json.loads(prop.raw_json or "{}")
    except Exception:
        raw = {}

    mls_comps = raw.get("mls_comps") or []
    if not mls_comps:
        flash("No MLS comps entered yet.", "warning")
        return redirect(url_for("main.mls_comps", property_id=property_id))

    subj = {
        "address":   prop.full_address or prop.address,
        "beds":      prop.beds,
        "baths":     prop.baths,
        "sqft":      prop.sqft,
        "yearBuilt": prop.year_built,
        "lat":       prop.lat,
        "lng":       prop.lng,
    }

    # top_k from form or default 6
    try:
        top_k = int(request.form.get("k") or 6)
    except Exception:
        top_k = 6

    picked, notes = [], ""
    try:
        picked, notes = choose_best_comps_with_ai(subj, mls_comps, k=top_k)
    except Exception as e:
        current_app.logger.exception("AI MLS comps selection failed; falling back to heuristic")
        picked = score_comps_heuristic(subj, mls_comps)[:top_k]
        notes = f"AI MLS selection failed: {e}. Heuristic selection applied."

    # Save AI-selected comps and mark Stage 3
    raw["comps_selected"] = picked
    raw["comps_selected_notes"] = notes
    raw["comps_source"] = "mls"

    # pipeline state
    prop.evaluation_stage = 3
    prop.needs_mls_review = False
    prop.mls_review_completed = datetime.utcnow()
    prop.raw_json = json.dumps(raw)

    db.session.add(prop)
    db.session.commit()

    flash(f"Selected {len(picked)} MLS comps and updated ARV stage.", "success")
    return redirect(url_for("main.property_detail", property_id=property_id))

@main.route("/va/needs_mls_review")
def va_needs_mls_review():
    # You can tweak this filter however you want
    props = (
        Property.query
        .filter_by(needs_mls_review=True)
        .order_by(Property.id.desc())
        .all()
    )
    return render_template("va_needs_mls_review.html", properties=props)





