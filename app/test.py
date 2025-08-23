from dotenv import load_dotenv
load_dotenv(r"C:\Users\lharwell\Desktop\python\Wholesale app\Wholesale app\.env")  # path to YOUR .env

from services import attom as attom_svc
test = attom_svc.sale_comps(lat=30.2672, lon=-97.7431, radius_miles=2.0)
print(test.get("status"), len(test.get("property", [])))


# app/test.py (or wherever you’re running it)
try:
    from services import attom as attom_svc      # if running from inside app/
except ImportError:
    from services import attom as attom_svc  # if running from project root

resp = attom_svc.sale_comps(lat=30.2672, lon=-97.7431, radius_miles=2.0)

# Sanity prints
print("keys:", list(resp.keys()))
print("status:", resp.get("status"))
print("num properties:", len(resp.get("property", [])))
