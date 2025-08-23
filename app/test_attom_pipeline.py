from dotenv import load_dotenv
load_dotenv(r"C:\Users\lharwell\Desktop\python\Wholesale app\Wholesale app\.env")  # path to YOUR .env

# app/test_attom_pipeline.py
from services import attom as A
raw = A.sale_comps(lat=30.2672, lon=-97.7431, radius_miles=0.5, page_size=50)
rows = A.extract_comps(raw, max_items=50)
good = A.filter_comps_rules(rows, subject_sqft=1600, subject_year=1978,
                            max_months=6, max_radius_miles=0.5,
                            sqft_tolerance=0.15, year_tolerance=5)
print("raw:", len(rows), "good:", len(good))
for c in good[:5]:
    print(c["saleDate"], c["price"], c["address"], c.get("docType"), c.get("transType"))
