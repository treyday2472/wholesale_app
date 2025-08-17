# app/services/melissa_deeds_test.py
import os, json
from .melissa_client import lookup_property, lookup_deeds, MelissaHttpError

# ---- set your test address here ----
ADDRESS = "3420 Anmar Ct"
CITY    = "Forest Hill"
STATE   = "TX"
POSTAL  = "76140"

def main():
    # Optionally set your key here (or export it in your shell):
    # os.environ["MELISSA_API_KEY"] = "<YOUR_LICENSE_KEY>"

    try:
        # 1) Resolve FIPS/APN via LookupProperty (most reliable key for deeds)
        prop = lookup_property(
            a1=ADDRESS, city=CITY, state=STATE, postal=POSTAL,
            ff=False, cols="GrpAll"
        )
        print("== LookupProperty raw ==")
        print(json.dumps(prop, indent=2))

        recs  = (prop or {}).get("Records") or []
        fips = apn = None
        if recs:
            parcel = (recs[0] or {}).get("Parcel") or {}
            fips = parcel.get("FIPSCode")
            apn  = parcel.get("UnformattedAPN") or parcel.get("FormattedAPN")

        # 2) Call LookupDeeds using FIPS+APN if we have them; else fall back to ff
        opt  = "Page:1,RecordsPerPage:5"  # paginate for readability
        cols = "GrpDocInfo,GrpTxDefInfo,GrpTxAmtInfo,GrpPrimaryGrantor,GrpPrimaryGrantee,GrpMortgage1"

        if fips and apn:
            deeds = lookup_deeds(fips=fips, apn=apn, opt=opt, cols=cols)
        else:
            ff = f"{ADDRESS} {CITY} {STATE} {POSTAL}".strip()
            deeds = lookup_deeds(ff=ff, opt=opt, cols=cols)

        print("\n== LookupDeeds raw ==")
        print(json.dumps(deeds, indent=2))

        # 3) Quick, human-readable summary
        dlist = (deeds or {}).get("Records") or []
        print(f"\nFound {len(dlist)} deed record(s)")
        for i, d in enumerate(dlist, start=1):
            doc  = d.get("DocInfo") or {}
            tx   = d.get("TxDefInfo") or {}
            amt  = d.get("TxAmtInfo") or {}
            gtor = d.get("PrimaryGrantor") or {}
            gtee = d.get("PrimaryGrantee") or {}
            mtg  = d.get("Mortgage1") or {}

            print(f"\n--- Deed #{i} ---")
            print("Recording date:",    doc.get("RecordingDate"))
            print("Instrument date:",   doc.get("InstrumentDate"))
            print("Instrument number:", doc.get("InstrumentNumber"))
            print("Doc type code:",     doc.get("TypeCode"))
            print("Transaction type:",  tx.get("TransactionType"))
            print("Arms length:",        tx.get("ArmsLengthFlag"))
            print("Quitclaim:",          tx.get("QuitclaimFlag"))
            print("Transfer amount:",    amt.get("TransferAmount"))
            print("Grantor:",            gtor.get("Name1Full"), "|", gtor.get("Name2Full"))
            print("Grantee:",            gtee.get("Name1Full"), "|", gtee.get("Name2Full"))
            print("Mortgage amount:",    mtg.get("Amount"))
            print("Mortgage lender:",    mtg.get("LenderFullName"))
            print("Interest rate:",      mtg.get("InterestRate"))
            print("Mortgage type:",      mtg.get("Type"))
            print("Mortgage rec date:",  mtg.get("RecordingDate"))

    except MelissaHttpError as e:
        print("Melissa error:", e)
    except Exception as e:
        print("Unexpected error:", e)

if __name__ == "__main__":
    main()
