# app/services/melissa_merge_test.py
import os, sys, json
from datetime import datetime

from .melissa_client import (
    lookup_property,
    lookup_deeds,
    normalize_property_record,
    MelissaHttpError,
)

def fmt_addr(address, city, state, zip_):
    parts = [p for p in [address, city, state, zip_] if p]
    return ", ".join(parts)

def main():
    # Accept a free-form address as a single CLI argument, or fall back to a hard-coded demo
    ff = " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else "3420 Anmar Ct, Forest Hill, TX 76140"

    print(f"\n>>> Using address: {ff}\n")

    try:
        # Prefer structured inputs for LookupProperty if we can split them
        a1, city, state, postal = ff, "", "", ""
        if "," in ff:
            try:
                line, tail = ff.split(",", 1)
                parts = tail.strip().split()
                st = parts[-2] if len(parts) >= 2 else ""
                z  = parts[-1] if parts and parts[-1].replace("-", "").isdigit() else ""
                if z:
                    city   = " ".join(parts[:-2])
                    state  = st
                    postal = z
                else:
                    city   = " ".join(parts[1:]) if len(parts) > 1 else (parts[0] if parts else "")
                    state  = st
                a1 = line.strip()
            except Exception:
                a1 = ff

        # 1) LookupProperty
        prop_payload = lookup_property(a1=a1, city=city, state=state, postal=postal, country="US", ff=False, cols="GrpAll")
        print("LookupProperty returned TotalRecords =", (prop_payload or {}).get("TotalRecords"))

        recs = (prop_payload or {}).get("Records") or []
        fips = apn = None
        if recs:
            r0 = recs[0]
            parcel = r0.get("Parcel") or {}
            fips = parcel.get("FIPSCode")
            apn  = parcel.get("UnformattedAPN") or parcel.get("FormattedAPN")

        # 2) LookupDeeds (prefer FIPS+APN; else free-form)
        if fips and apn:
            deeds_payload = lookup_deeds(fips=fips, apn=apn, opt="Page:1,RecordsPerPage:5")
        else:
            deeds_payload = lookup_deeds(ff=ff, opt="Page:1,RecordsPerPage:5")

        print("LookupDeeds returned TotalRecords =", (deeds_payload or {}).get("TotalRecords"))


        

        # 3) Merge exactly like your route
        raw = {}
        raw.setdefault("melissa", {})
        raw["melissa"]["LookupProperty"] = prop_payload
        if deeds_payload is not None:
            raw["melissa"]["LookupDeeds"] = deeds_payload

        # 4) Quick human summary (owner, lot size, flags) + normalized view
        owner_name = owner_addr = ""
        lot_sf = None
        prop_full_addr = ""

        if recs:
            r0 = recs[0]
            primary_owner = r0.get("PrimaryOwner") or {}
            owner_name = primary_owner.get("Name1Full") or ""
            owner_name2 = primary_owner.get("Name2Full") or ""
            
            secondary_owner = r0.get("SecondaryOwner")
            owner_name3 = secondary_owner.get("Name3Full")

            oaddr = r0.get("OwnerAddress") or {}
            owner_addr = fmt_addr(oaddr.get("Address"), oaddr.get("City"), oaddr.get("State"), oaddr.get("Zip"))


            pa = r0.get("PropertyAddress") or {}
            prop_full_addr = fmt_addr(pa.get("Address"), pa.get("City"), pa.get("State"), pa.get("Zip"))

            size = r0.get("PropertySize") or {}
            lot_sf = size.get("AreaLotSF")

            Property_use_info = r0.get("PropertyUseInfo") or {}
            type = Property_use_info.get("PropertyUseGroup") or {}

            property_size = r0.get("PropertySize") or {}
            parking_garage = property_size.get("ParkingGarage") or {}
            parking_garage_area = property_size.get("ParkingGarageArea") or {}

            tax = r0.get("Tax") or {}
            year_assessed = tax.get("YearAssessed") or {}
            assessed_value_total = tax.get("AssessedValueTotal") or {}
            tax_billed_amount = tax.get("TaxBilledAmount") or {}

            legal = r0.get("Legal") or {}
            legal_description = legal.get("LegalDescription") or {}
            county = parcel.get("County") or {}

            subdivision = legal.get("Subdivision") or {}
            block = legal.get("Block1") or {}
            LotNumber = legal.get("LotNumber") or {}

            sale_info = r0.get("SaleInfo") or {}
            assessor_last_sale_date = sale_info.get("AssessorLastSaleDate") or {}
            assessor_last_sale_amount = sale_info.get("AssessorLastSaleAmount") or {}

            int_room_info = r0.get("IntRoomInfo") or {}
            bath_count = r0.get("BathCount") or {}
            partial_bath_count = int_room_info.get("PartialBathCount") or {}
            primary_grantor = r0.get("PrimaryGrantor") or {}
            grantor1 = primary_grantor.get("name1full") or {}



        print("\n--- Merge preview (selected fields) ---")
        print("Property address  :", prop_full_addr or "(n/a)")
        print("full baths        :", bath_count or "n/a")
        print("half baths        :", partial_bath_count or "n/a")
        print("Lot size (sq ft)  :", lot_sf or "(n/a)")
        print("Garage            :", parking_garage or "n/a)")
        print("Garage_size       :", parking_garage_area or "n/a")

        print("Owner name        :", owner_name or "(n/a)")
        print("Owner name2       :", owner_name2 or "(n/a)")
        print("Owner name3       :", owner_name3 or "(n/a)")
        print("Owner address     :", owner_addr or "(n/a)")

        print("Year Assessed     :", year_assessed or "n/a")
        print("asssessed Amount  :", assessed_value_total or "n/a")
        print("annual tax bill   :", tax_billed_amount or "n/a")

        print("Last Sale Date    :", assessor_last_sale_date or "n/a")
        print("last_sale_amount  :", assessor_last_sale_amount or "n/a")
        print("type              :", type or "n/a")
        print("legal description :", legal_description or "n/a")
        print("grantor1          :", grantor1 or "n/a")



              


        # Normalize (same call you use in routes) so you can see the final shape
        normalized = {}
        if recs:
            normalized = normalize_property_record(recs[0], deeds_payload=deeds_payload) or {}
        print("\n--- normalize_property_record(...) sample keys ---")
        print(list(normalized.keys()))

        # 5) Write to file so you can inspect
        out = {
            "merged_raw": raw,           # EXACT structure you store in prop.raw_json["melissa"]
            "normalized": normalized,    # What you promote or use for UI
            "meta": {
                "address_input": ff,
                "generated_at": datetime.utcnow().isoformat() + "Z",
            }
        }
        outfile = f"melissa_merge_preview_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.json"
        with open(outfile, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print(f"\nWrote merged preview to {outfile}\n")

    except MelissaHttpError as e:
        print("Melissa error:", e)
    except Exception as e:
        print("Unexpected error:", e)



if __name__ == "__main__":
    main()
