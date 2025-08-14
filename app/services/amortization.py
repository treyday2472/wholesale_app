from datetime import date, datetime
from math import pow

def _to_date(d):
    if not d: return None
    if isinstance(d, (date, datetime)): return d.date() if isinstance(d, datetime) else d
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try: return datetime.strptime(d, fmt).date()
        except Exception: pass
    return None

def estimate_balance(orig_amount: float, rate_pct: float, orig_date, term_months: int,
                     as_of: date | None = None, payment: float | None = None) -> float | None:
    """
    Basic amortization estimate. Returns None if inputs are insufficient.
    """
    if not orig_amount or not rate_pct or not term_months:
        return None
    start = _to_date(orig_date)
    if not start: return None
    today = as_of or date.today()
    months_elapsed = (today.year - start.year) * 12 + (today.month - start.month)
    if months_elapsed <= 0: return orig_amount

    r = float(rate_pct) / 100.0 / 12.0
    n = int(term_months)

    # compute scheduled payment if not provided
    pmt = payment
    if not pmt:
        try:
            pmt = (orig_amount * r) / (1 - pow(1 + r, -n))
        except Exception:
            return None

    # remaining balance after k payments:
    k = min(months_elapsed, n)
    try:
        bal = orig_amount * pow(1 + r, k) - pmt * ( (pow(1 + r, k) - 1) / r )
        return max(bal, 0.0)
    except Exception:
        return None
