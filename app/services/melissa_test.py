# app/services/melissa_test.py
import os, json
from .melissa_client import lookup_property, MelissaHttpError

# If you prefer, set the key in your environment instead of here:
# os.environ["MELISSA_API_KEY"] = "<YOUR_LICENSE_KEY>"

def main():
    try:
        # IMPORTANT: You passed addr="3420" and a1="anmar ct" earlier.
        # Because you passed a1, your function ignores addr — so the house number was lost.
        # Use a single string in a1 OR use ff="<full address>". Also ask for everything with GrpAll.
        result = lookup_property(
            a1="3420 Anmar Ct",           # full line 1 (house no + street)
            city="Forest Hill",
            state="TX",
            postal="76140",
            ff=False,                     # we’re giving a1/city/state/postal, so ff is not needed
            cols="GrpAll"                 # return all fields so you can see everything
        )

        print(json.dumps(result, indent=2))

        # A few quick pulls so you can see key bits at a glance:
        recs = (result or {}).get("Records") or []
        if recs:
            rec = recs[0]
            parcel = rec.get("Parcel") or {}
            size   = (rec.get("PropertySize") or {}).get("AreaLotSF")
            owner  = rec.get("PrimaryOwner") or {}
            oaddr  = rec.get("OwnerAddress") or {}
            legal_desc = rec.get("Legal") or {}

            owner_occupied = (oaddr.get("OwnerOccupied","").lower() == "y")
            business_owned = (owner.get("CompanyFlag","").upper() == "Y")

            print("\n--- Quick summary ---")
            print("APN:", parcel.get("UnformattedAPN") or parcel.get("FormattedAPN"))
            print("Lot (sq ft):", size)
            print("Owner:", owner.get("Name1Full"))
            print("Owner address:", oaddr.get("Address"), oaddr.get("City"), oaddr.get("State"), oaddr.get("Zip"))
            print("Owner occupied:", owner_occupied)
            print("Business owned:", business_owned)
            print("Legal Description:", legal_desc.get("LegalDescription"))

    except MelissaHttpError as e:
        print("Melissa error:", e)
    except Exception as e:
        print("Unexpected error:", e)

if __name__ == "__main__":
    main()
