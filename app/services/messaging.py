# app/services/messaging.py
def send_offer_sms(lead, offer):
    if not lead.phone:
        return
    msg = (
        f"Hi {lead.seller_first_name or ''}, prelim offer for {lead.address}: "
        f"${offer.my_cash_offer:,.0f} (ARV ${offer.arv:,.0f}, repairs ${offer.repairs_flip:,.0f}). "
        "Reply to discuss or send photos for a firm offer."
    )
    # TODO: call Vonage SMS API here
    print("SMS ->", lead.phone, ":", msg)
