
## /api/evaluate
POST body:
{
  "facts": {
    "address": "2631 Jonesboro Ave, Dallas, TX",
    "arv": 218000,
    "repairs": 9810,
    "monthly_taxes": 345,
    "insurance": 109,
    "cash_offer": 150000,
    "investor_cash_price": 160230,
    "reinstatement_amount": 0,
    "cash_for_equity": 0
  }
}
Returns both `base` (ARV/repairs/MAO) and `exits` (wholesale/flip/owner-finance/lease/sub2/land).
