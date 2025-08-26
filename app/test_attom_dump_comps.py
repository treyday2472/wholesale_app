# app/test_attom_dump_comps.py
from dotenv import load_dotenv
load_dotenv(r"C:\Users\lharwell\Desktop\python\Wholesale app\Wholesale app\.env")

from datetime import datetime
from services import attom as A
import json, csv, os

# ---------------- subject ----------------
ADDR1 = "16437 Aventura Ave"
CITY  = "Pflugerville"
STATE = "TX"
ZIP   = "78660"

RADIUS_MILES = 1.5
PAGE_SIZE    = 100

# ---------------- helpers ----------------
def g(d, path, default=None):
    """safe nested get: g(row, 'sale.amount.saleamt')"""
    cur = d
    for key in path.split('.'):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur

def _usd(n):
    try:
        return f"${float(n):,.0f}"
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

_ALLOWED_DEED_TOKENS = ("DEED", "GRANT DEED", "WARRANTY DEED", "SPECIAL WARRANTY DEED", "QUIT CLAIM DEED")
_EXCLUDED_DOC_TOKENS = ("MORTGAGE", "DEED OF TRUST", "ASSIGNMENT", "RELEASE", "LIEN", "UCC", "FORECLOSURE")

def is_deed_doc(doc):
    if not doc: return False
    d = str(doc).upper()
    if any(x in d for x in _EXCLUDED_DOC_TOKENS): return False
    return any(x in d for x in _ALLOWED_DEED_TOKENS) or d.strip() == "DEED"

def parse_date_any(s):
    if not s:
        return None
    s = str(s).strip()
    s10 = s[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s10, fmt).date()
        except Exception:
            pass
    return None

def date_human(s):
    d = parse_date_any(s)
    if not d:
        return "—"
    today = datetime.utcnow().date()
    delta = (today - d).days
    if delta < 1: rel = "today"
    elif delta < 30: rel = f"{delta}d ago"
    elif delta < 365: rel = f"{delta//30}mo ago"
    else: rel = f"{delta//365}yr ago"
    return f"{d:%Y-%m-%d} ({rel})"

# ---------------- pull subject + snapshot ----------------
detail = A.property_detail(address1=ADDR1, city=CITY, state=STATE, postalcode=ZIP)
lat, lon = A.extract_detail_coords(detail)
subj = A.extract_detail_basics(detail) or {}
print("Subject:", {
    "sqft": subj.get("sqft"),
    "yearBuilt": subj.get("yearBuilt"),
    "lat": lat, "lon": lon
})

if lat and lon:
    snap = A.sale_comps(lat=lat, lon=lon, radius_miles=RADIUS_MILES, page_size=PAGE_SIZE)
else:
    snap = A.sale_comps(address1=ADDR1, city=CITY, state=STATE, postalcode=ZIP,
                        radius_miles=RADIUS_MILES, page_size=PAGE_SIZE)

props = (snap or {}).get("property") or []
print(f"Snapshot properties: {len(props)}\n")

# ---------------- print human-readable summary for EVERY row ----------------
def summarize_row(i, p):
    a = p.get("address", {}) or {}
    b = p.get("building", {}) or {}
    rooms = b.get("rooms", {}) or {}
    size  = b.get("size", {}) or {}
    summ  = (p.get("summary") or b.get("summary") or {}) or {}
    loc   = p.get("location", {}) or {}
    sale  = p.get("sale", {}) or p.get("saleSearch", {}) or {}
    amt   = sale.get("amount", {}) or {}

    addr   = a.get("oneLine") or ", ".join([x for x in [a.get("line1"), a.get("locality"), a.get("countrySubd"), a.get("postal1")] if x])
    beds   = rooms.get("beds") or b.get("bedrooms")
    baths  = rooms.get("bathsfull") or rooms.get("bathsFull") or rooms.get("bathstotal") or b.get("bathrooms")
    sqft   = size.get("livingsize") or size.get("bldgsize") or size.get("universalsize") or size.get("grossSize")
    year   = summ.get("yearbuilt") or b.get("yearbuilt") or b.get("yearBuilt")
    dist   = loc.get("distance")

    # all sale fields we care about
    sale_doc  = amt.get("saledoctype") or sale.get("saledoctype") or sale.get("saleDocType")
    sale_type = sale.get("saletranstype") or sale.get("saleTransType")
    sale_amt  = amt.get("saleamt") or sale.get("saleamt") or sale.get("saleAmt")
    sdate     = sale.get("saleTransDate") or sale.get("saleDate") or sale.get("salerecdate") or sale.get("salesearchdate")

    print(f"--- #{i} ---")
    print(f"addr : {addr}")
    print(f"use  : {summ.get('propLandUse') or summ.get('propertyType') or summ.get('proptype') or '—'}")
    print(f"beds : {_num(beds)} | baths: {_num(baths)} | sqft: {_sf(sqft)} | year: {_num(year)} | dist(mi): {_num(dist)}")
    print(f"sale : date={date_human(sdate)} | amt={_usd(sale_amt)} | doc={sale_doc or '—'} | type={sale_type or '—'} | deed?={'Y' if is_deed_doc(sale_doc) else 'N'}")
    # if you want to see which sale keys exist at a glance:
    present = [k for k in ("saleTransDate","saleDate","salerecdate","salesearchdate","saleamt","saleAmt","saledoctype","saleDocType","saletranstype") if g(sale, k) or g(amt, k)]
    print(f"keys : {present}\n")

for i, p in enumerate(props, 1):
    summarize_row(i, p)

# ---------------- write full raw JSON & flat CSV ----------------
stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
json_path = f"comps_raw_dump_{stamp}.json"
csv_path  = f"comps_raw_dump_{stamp}.csv"

with open(json_path, "w", encoding="utf-8") as f:
    json.dump(props, f, indent=2)

fields = [
    "address.oneLine", "location.distance",
    "building.rooms.beds", "building.bedrooms",
    "building.rooms.bathsfull", "building.rooms.bathsFull", "building.rooms.bathstotal", "building.bathrooms",
    "building.size.livingsize", "building.size.bldgsize", "building.size.universalsize", "building.size.grossSize",
    "summary.yearbuilt", "building.yearbuilt", "building.yearBuilt",
    "sale.saleTransDate", "sale.saleDate", "sale.salerecdate", "sale.salesearchdate",
    "sale.amount.saleamt", "sale.saleamt", "sale.saleAmt",
    "sale.amount.saledoctype", "sale.saledoctype", "sale.saleDocType",
    "sale.saletranstype", "sale.saleTransType",
    "summary.propLandUse", "summary.propertyType", "summary.proptype",
]
with open(csv_path, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["#"] + fields)
    for i, p in enumerate(props, 1):
        w.writerow([i] + [g(p, fld) for fld in fields])

print(f"Wrote raw JSON: {os.path.abspath(json_path)}")
print(f"Wrote flat CSV: {os.path.abspath(csv_path)}")
