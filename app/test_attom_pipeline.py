# app/test_attom_pipeline_aventura.py

from dotenv import load_dotenv
load_dotenv(r"C:\Users\lharwell\Desktop\python\Wholesale app\Wholesale app\.env")

from datetime import datetime
from services import attom as A
import sys

# ---------------- Pretty helpers ----------------

def _usd(n):
    try:
        return f"${float(n):,.0f}"
    except Exception:
        return "—"

def _psf(price, sqft):
    try:
        p = float(price)
        s = float(sqft)
        if p > 0 and s > 0:
            return f"${p/s:,.0f}"
    except Exception:
        pass
    return "—"

def _mi(x):
    try:
        return f"{float(x):.2f}"
    except Exception:
        return "—"

def _sf(x):
    try:
        return f"{int(float(x)):,}"
    except Exception:
        return "—"

def _num(x):
    if x is None:
        return "—"
    try:
        f = float(x)
        return str(int(f)) if f.is_integer() else f"{f:.1f}"
    except Exception:
        return str(x)

def _parse_date_any_local(s):
    """Fallback if services.attom._parse_date_any is missing."""
    if not s:
        return None
    s = str(s).strip()
    s10 = s[:10]
    for fmt in ("%Y-%m-%d", "%Y-%m", "%m/%d/%Y"):
        try:
            return datetime.strptime(s10, fmt).date()
        except Exception:
            pass
    return None

def _date_human(s):
    """
    '2025-02-26 (5mo ago)' or '—'
    Uses A._parse_date_any when available, otherwise falls back locally.
    """
    try:
        d = A._parse_date_any(s) if hasattr(A, "_parse_date_any") else _parse_date_any_local(s)
        if not d:
            return "—"
        today = datetime.utcnow().date()
        delta = (today - d).days
        if delta < 1:
            rel = "today"
        elif delta < 30:
            rel = f"{delta}d ago"
        elif delta < 365:
            rel = f"{delta//30}mo ago"
        else:
            rel = f"{delta//365}yr ago"
        return f"{d:%Y-%m-%d} ({rel})"
    except Exception:
        return str(s) if s else "—"

def print_comps_table(comps, limit=25, show_doctype=False, title=None):
    if title:
        print(title)
    hdr = f"{'Sale date':<20} {'Price':>12} {'$/sf':>8} {'Bd':>3} {'Ba':>4} {'Sqft':>7} {'Mi':>6}  Address"
    if show_doctype:
        hdr += "  [Doc]"
    print(hdr)
    print("-" * len(hdr))
    for c in comps[:limit]:
        date_s = _date_human(c.get("saleDate"))
        price  = _usd(c.get("price"))
        psf    = _psf(c.get("price"), c.get("sqft"))
        bd     = _num(c.get("beds"))
        ba     = _num(c.get("baths"))
        sqft   = _sf(c.get("sqft"))
        dist   = _mi(c.get("distance"))
        addr   = c.get("address") or ""
        line = f"{date_s:<20} {price:>12} {psf:>8} {bd:>3} {ba:>4} {sqft:>7} {dist:>6}  {addr}"
        if show_doctype:
            line += f"  [{(c.get('docType') or '—')}]"
        print(line)
    print()

# ---------------- Config (adjust as you like) ----------------

ADDR1 = "16437 Aventura Ave"   # <— correct spelling
CITY  = "Pflugerville"
STATE = "TX"
ZIP   = "78660"

RADIUS_MILES   = 1.5
PAGE_SIZE      = 100
MAX_MONTHS     = 120           # e.g., 10y window for testing; tighten later
SQFT_TOL       = 0.25          # ±25%
YEAR_TOL       = 10            # ±10 years
REQ_SUBDIVISION = False

# ---------------- 1) Subject basics ----------------

try:
    detail = A.property_detail(address1=ADDR1, city=CITY, state=STATE, postalcode=ZIP)
except Exception as e:
    print("property_detail failed:", e)
    sys.exit(1)

subj   = A.extract_detail_basics(detail) or {}
lat, lon = A.extract_detail_coords(detail)

subject_sqft = subj.get("sqft")
subject_year = subj.get("yearBuilt")

print("Subject basics:", {
    "sqft": subject_sqft, "yearBuilt": subject_year, "lat": lat, "lon": lon
})

# ---------------- 2) Pull comps snapshot ----------------

try:
    if lat and lon:
        snap = A.sale_comps(lat=lat, lon=lon, radius_miles=RADIUS_MILES, page_size=PAGE_SIZE)
    else:
        snap = A.sale_comps(address1=ADDR1, city=CITY, state=STATE, postalcode=ZIP,
                            radius_miles=RADIUS_MILES, page_size=PAGE_SIZE)
except Exception as e:
    print("sale_comps failed:", e)
    sys.exit(1)

rows = A.extract_comps(snap, max_items=PAGE_SIZE) or []
print("Rows total:", len(rows))

# ---------------- 3) Basic residential-ish filter ----------------

def _to_float(x):
    try:
        return float(x)
    except Exception:
        return None

def likely_residential(r):
    # treat as residential if (beds present) OR (sqft <= 8000)
    s = _to_float(r.get("sqft"))
    return (r.get("beds") is not None) or (s is not None and s <= 8000)

resi_rows = [r for r in rows if likely_residential(r)]
print("Residential-ish:", len(resi_rows))

# ---------------- 4) Rule-based filter (using your attom.filter_comps_rules) ----------------

kept = A.filter_comps_rules(
    resi_rows,
    subject_sqft=subject_sqft or None,
    subject_year=subject_year or None,
    max_months=MAX_MONTHS,
    max_radius_miles=RADIUS_MILES,
    sqft_tolerance=SQFT_TOL,
    year_tolerance=YEAR_TOL,
    require_subdivision=REQ_SUBDIVISION,
)

print("Kept comps:", len(kept))

closed_sales = [c for c in kept if c.get("price")]
print("Closed sales w/ price:", len(closed_sales))

# ---------------- 5) Pretty print ----------------

if closed_sales:
    print_comps_table(closed_sales, limit=25, show_doctype=True, title="Closed Sales (top 25):")
else:
    print_comps_table(kept, limit=25, show_doctype=True, title="Matches (no price in feed) (top 25):")
