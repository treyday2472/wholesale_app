ASSUMPTIONS = {
    'offer_pct': 0.70,
    'wholetail_pct': 0.82,
    'wholesale_fee': 10000,
    'buffer': 5000,
    'rent_vacancy': 0.05,
    'pm_pct': 0.08,
    'maint_pct': 0.08,
    'taxes_pct_of_value': 0.015,
    'ins_pct_of_value': 0.006,
    'capex_pct': 0.05,
    'interest_rate': 0.075,
    'down_pct_rental': 0.25,
    'lease_option_fee_pct': 0.03,
    'lease_rent_credit_pct': 0.15,
    'lease_strike_pct_of_arv': 0.95,
    'lease_term_years': 3,
    'land_baseline_ppa': 15000,
    'land_wholesale_pct': 0.60,
    'land_retail_pct': 0.85,
}

import json, os
import requests

def geocode_address(address, google_api_key: str):
    """Use Google Geocoding to normalize -> (lat,lng, formatted_address) or (None,None,address)."""
    if not google_api_key:
        return None, None, address
    try:
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"address": address, "key": google_api_key}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        if data.get("results"):
            res = data["results"][0]
            loc = res["geometry"]["location"]
            return loc["lat"], loc["lng"], res.get("formatted_address", address)
        return None, None, address
    except Exception:
        return None, None, address

def fetch_property_facts(address, lat=None, lng=None, rapidapi_key: str = ""):
    """
    Placeholder: hook Zillow/ATTOM/Estated/Melissa/etc.
    Return a dict with any facts we can find. For now, return empty facts.
    """
    # TODO: wire a real data source. For now return defaults.
    return {
        "beds": None,
        "baths": None,
        "sqft": None,
        "lot_size": None,
        "year_built": None,
        "school_district": None,
        "zpid": None,
        "raw": {},
    }

def find_comps_and_arv(lat, lng, beds=None, baths=None, rapidapi_key: str = ""):
    """
    Placeholder comps: here’s where you’d call an API or your own dataset.
    Return comps list + arv estimate (median of sale prices).
    """
    comps = []  # list of dicts: {address, distance_mi, beds, baths, sqft, sold_price, sold_date}
    arv = None
    # TODO: implement real comps logic from your chosen source.
    return comps, arv

def evaluate_property(address, full_address, lat, lng, google_key, rapid_key):
    # Normalize location if needed
    if (lat is None or lng is None) and address:
        lat, lng, full_address = geocode_address(address, google_key)

    facts = fetch_property_facts(full_address or address, lat, lng, rapid_key)
    comps, arv = find_comps_and_arv(lat, lng, facts.get("beds"), facts.get("baths"), rapid_key)

    result = {
        "address": address,
        "full_address": full_address or address,
        "lat": lat, "lng": lng,
        "facts": facts,
        "comps": comps,
        "arv": arv
    }
    return result

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

@dataclass
class EvalResult:
    arv: float
    repairs: float
    mao_cash: float
    wholetail_offer: float
    notes: str
    rental_summary: Dict[str, Any]
    comps_used: List[Dict[str, Any]]

def evaluate_property(property_facts: Dict[str, Any], comps: Optional[List[Dict[str, Any]]] = None) -> EvalResult:
    # Minimal placeholder if your earlier version isn't present in this file
    arv = float(property_facts.get('zestimate') or property_facts.get('arv') or 0)
    repairs = float(property_facts.get('repairs') or 0)
    mao_cash = max(0, round(arv * 0.70 - repairs - 10000 - 5000, 0))
    wholetail_offer = max(0, round(arv * 0.85 - repairs*0.5 - 5000, 0))
    return EvalResult(arv=arv, repairs=repairs, mao_cash=mao_cash, wholetail_offer=wholetail_offer, notes='', rental_summary={}, comps_used=comps or [])


def _pmt(principal, annual_rate, years):
    r = annual_rate/12.0
    n = years*12
    if r == 0:
        return principal / n
    return principal * (r) / (1 - (1 + r)**(-n))

def _remaining_balance(payment, annual_rate, years_elapsed, total_years):
    r = annual_rate/12.0
    n_paid = years_elapsed*12
    n_total = total_years*12
    if r == 0:
        orig_principal = payment * n_total
        principal_paid = payment * n_paid
        return max(0.0, orig_principal - principal_paid)
    return payment * (1 - (1 + r)**(- (n_total - n_paid))) / r

def calc_wholesale_cash(arv, repairs, investor_cash_price=None, cash_offer=None):
    suggested_cash_offer = (arv * 0.64) - repairs
    fee = None
    if investor_cash_price is not None and cash_offer is not None:
        fee = investor_cash_price - cash_offer
    return {
        "suggested_cash_offer_64_rule": round(suggested_cash_offer, 0),
        "potential_wholesale_fee_cash": round(fee, 0) if fee is not None else None
    }

def calc_flip(arv, real_repairs_rental_of=0.0, est_insurance=0.0, monthly_taxes=0.0):
    end_buyer_sale_price = (arv * 0.90) - real_repairs_rental_of
    loan_points_fees = 2 * (arv / 100.0)
    loan_payments_fees = arv * (0.10 / 12.0)
    short_term_holding = 2200 + est_insurance + monthly_taxes
    loan_costs = loan_points_fees + (loan_payments_fees * 4)
    return {
        "end_buyer_sale_price": round(end_buyer_sale_price, 0),
        "loan_points_fees": round(loan_points_fees, 0),
        "loan_payments_fees_monthly": round(loan_payments_fees, 0),
        "short_term_flip_holding_costs": round(short_term_holding, 0),
        "loan_costs": round(loan_costs, 0)
    }

def calc_owner_finance_tier(arv, repairs, pct_of_arv, down_pct=None, rate=0.05, term_years=30, balance_after_year=25, amount_override=None):
    price = (arv * pct_of_arv) - repairs
    if amount_override is not None:
        amount_financed = amount_override
        down = None
    else:
        down = price * (down_pct or 0.0)
        amount_financed = price - (down or 0.0)
    payment = _pmt(amount_financed, rate, term_years)
    balance_after = _remaining_balance(payment, rate, balance_after_year, term_years)
    return {
        "price": round(price, 0),
        "down_payment": round(down, 0) if down is not None else None,
        "amount_financed": round(amount_financed, 0),
        "monthly_payment_pi": round(payment, 0),
        "balance_after_term_year": balance_after_year,
        "balance_after_term": round(balance_after, 0),
        "rate": rate,
        "term_years": term_years
    }

def calc_owner_finance_all(arv, repairs, lease_option_B=None):
    ofA = calc_owner_finance_tier(arv, repairs, pct_of_arv=0.90, down_pct=None, rate=0.05, term_years=30, balance_after_year=25)
    if lease_option_B is not None:
        amount_financed_B = lease_option_B - ( (arv * 0.85 - repairs) * 0.05 )
        ofB = calc_owner_finance_tier(arv, repairs, pct_of_arv=0.85, down_pct=0.05, rate=0.05, term_years=30, balance_after_year=25, amount_override=amount_financed_B)
    else:
        ofB = calc_owner_finance_tier(arv, repairs, pct_of_arv=0.85, down_pct=0.05, rate=0.05, term_years=30, balance_after_year=25)
    ofC = calc_owner_finance_tier(arv, repairs, pct_of_arv=0.80, down_pct=0.10, rate=0.045, term_years=30, balance_after_year=25)
    ofD = calc_owner_finance_tier(arv, repairs, pct_of_arv=0.80, down_pct=0.50, rate=0.04, term_years=30, balance_after_year=25)
    return {"A": ofA, "B": ofB, "C": ofC, "D": ofD}

def calc_subject_to(real_fmv, reinstatement_amount=0.0, cash_for_equity=0.0):
    fee = (real_fmv * 0.18) - reinstatement_amount - cash_for_equity
    return {"potential_wholesale_fee_sub2": round(fee, 0)}

def calc_lease_option_placeholder(arv, repairs):
    price_B = (arv * 0.85) - repairs
    return {"anchor_price": round(price_B, 0)}

def calc_land_placeholder(arv=None, acres=None):
    return {"note": "Land valuation not implemented yet."}

def evaluate_exit_strategies(facts: dict):
    arv = float(facts.get("arv") or facts.get("ARV") or facts.get("arv_manual") or facts.get("zestimate") or 0.0)
    repairs_flip = float(facts.get("repairs_flip") or facts.get("real_repairs_flip") or facts.get("Real Repairs (flip)") or 0.0)
    repairs_rental_of = float(facts.get("repairs_rental_of") or facts.get("Real Repairs (rental/OF)") or 0.0)
    investor_cash_price = facts.get("investor_cash_price")
    cash_offer = facts.get("cash_offer")
    monthly_taxes = float(facts.get("monthly_taxes") or facts.get("Monthly Taxes") or 0.0)
    est_insurance = float(facts.get("insurance") or facts.get("Estimated Insurance") or 0.0)
    reinstatement = float(facts.get("reinstatement_amount") or facts.get("Reinstatement Amount") or 0.0)
    cash_for_equity = float(facts.get("cash_for_equity") or facts.get("Cash for Equity") or 0.0)

    real_fmv = arv - repairs_flip

    wholesale = calc_wholesale_cash(arv, repairs_flip, investor_cash_price, cash_offer)
    flip = calc_flip(arv, real_repairs_rental_of=repairs_rental_of, est_insurance=est_insurance, monthly_taxes=monthly_taxes)
    owner_finance = calc_owner_finance_all(arv, repairs_flip)
    sub2 = calc_subject_to(real_fmv, reinstatement, cash_for_equity)
    lease_option = calc_lease_option(arv, repairs_flip, market_rent=facts.get('market_rent'), assumptions=facts.get('assumptions'))
    land = calc_land_value(acres=facts.get('acres') or 0, baseline_ppa=(facts.get('ppa') or facts.get('baseline_ppa')), utilities=facts.get('utilities') or {}, assumptions=facts.get('assumptions'))

    return {
        "arv": round(arv, 0),
        "repairs_flip": round(repairs_flip, 0),
        "repairs_rental_of": round(repairs_rental_of, 0),
        "real_fmv": round(real_fmv, 0),
        "wholesale": wholesale,
        "flip": flip,
        "owner_finance": owner_finance,
        "lease_option": lease_option,
        "subject_to": sub2,
        "land": land
    }


def _merge_assumptions(overrides: dict = None):
    merged = dict(ASSUMPTIONS)
    if overrides and isinstance(overrides, dict):
        for k, v in overrides.items():
            if v is not None:
                merged[k] = v
    return merged


def calc_lease_option(arv, repairs, market_rent=None, assumptions: dict=None):
    A = _merge_assumptions(assumptions)
    # Anchor purchase/strike derived from ARV less repairs (similar to OF-B anchor)
    strike_price = (arv * A['lease_strike_pct_of_arv']) - repairs
    option_fee = strike_price * A['lease_option_fee_pct']
    rent = float(market_rent or 0)
    monthly_credit = rent * A['lease_rent_credit_pct'] if rent else 0
    term_months = int(A['lease_term_years'] * 12)
    total_credits = monthly_credit * term_months
    # Buyer cash to close on exercise (ignoring closing costs): strike - option - credits
    cash_needed_on_exercise = max(0, strike_price - option_fee - total_credits)
    return {
        "strike_price": round(strike_price, 0),
        "option_fee": round(option_fee, 0),
        "monthly_rent": round(rent, 0) if rent else None,
        "monthly_credit": round(monthly_credit, 0) if rent else None,
        "term_months": term_months,
        "total_credits": round(total_credits, 0),
        "cash_needed_on_exercise": round(cash_needed_on_exercise, 0),
        "assumptions_used": {
            "lease_option_fee_pct": A['lease_option_fee_pct'],
            "lease_rent_credit_pct": A['lease_rent_credit_pct'],
            "lease_strike_pct_of_arv": A['lease_strike_pct_of_arv'],
            "lease_term_years": A['lease_term_years'],
        }
    }


def calc_land_value(acres: float, baseline_ppa: float=None, utilities: dict=None, assumptions: dict=None):
    """Very simple land valuation:
    value = acres * baseline_ppa * utility_multiplier
    utility multipliers (multiplicative): road(1.1), water(1.1), sewer(1.15), electric(1.05), frontage(1.05).
    """
    A = _merge_assumptions(assumptions)
    ppa = float(baseline_ppa or A['land_baseline_ppa'])
    acres = float(acres or 0)
    util = utilities or {}
    mult = 1.0
    if util.get('road_access'): mult *= 1.10
    if util.get('water'):       mult *= 1.10
    if util.get('sewer'):       mult *= 1.15
    if util.get('electric'):    mult *= 1.05
    if util.get('frontage'):    mult *= 1.05
    est_value = acres * ppa * mult
    return {
        "est_value": round(est_value, 0),
        "wholesale_offer": round(est_value * A['land_wholesale_pct'], 0),
        "retail_price": round(est_value * A['land_retail_pct'], 0),
        "assumptions_used": {"ppa": ppa, "multiplier": round(mult, 3)}
    }
