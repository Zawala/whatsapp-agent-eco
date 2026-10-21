"""Knowledge source: bundles and FAQs. (Not named catalogue.py: that shadows a spaCy dependency.)

SAMPLE DATA ONLY. Names and prices are invented for the prototype and are not
real products. This module is the single source of truth for prices;
every document carries an effective window so stale content is never served.
"""
from datetime import date

BUNDLES = [
    {"id": "data-1gb", "title": "1GB Daily Data Bundle", "price_usd": 1.00,
     "keywords": ["1gb", "daily", "data"],
     "text": "1GB of data valid for 24 hours. Costs $1.00. Dial *143# to buy.",
     "effective_from": date(2026, 1, 1), "expires": None},
    {"id": "data-5gb", "title": "5GB Weekly Data Bundle", "price_usd": 4.50,
     "keywords": ["5gb", "weekly", "data"],
     "text": "5GB of data valid for 7 days. Costs $4.50. Dial *143# to buy.",
     "effective_from": date(2026, 1, 1), "expires": None},
    {"id": "data-20gb", "title": "20GB Monthly Data Bundle", "price_usd": 15.00,
     "keywords": ["20gb", "monthly", "data"],
     "text": "20GB of data valid for 30 days. Costs $15.00. Dial *143# to buy.",
     "effective_from": date(2026, 1, 1), "expires": None},
    {"id": "voice-100", "title": "100 Minutes Voice Bundle", "price_usd": 2.00,
     "keywords": ["voice", "minutes", "calls", "100"],
     "text": "100 on-net minutes valid for 7 days. Costs $2.00. Dial *143# to buy.",
     "effective_from": date(2026, 1, 1), "expires": None},
    {"id": "social-wa", "title": "WhatsApp Social Bundle", "price_usd": 0.50,
     "keywords": ["whatsapp", "social"],
     "text": "WhatsApp-only data valid for 7 days. Costs $0.50. Dial *143# to buy.",
     "effective_from": date(2026, 1, 1), "expires": None},
    # Superseded price: kept to prove stale documents are filtered out.
    {"id": "data-5gb-old", "title": "5GB Weekly Data Bundle", "price_usd": 4.00,
     "keywords": ["5gb", "weekly", "data"],
     "text": "5GB of data valid for 7 days. Costs $4.00. Dial *143# to buy.",
     "effective_from": date(2025, 1, 1), "expires": date(2025, 12, 31)},
]

FAQS = [
    {"id": "faq-check-balance", "title": "How to check your balance",
     "keywords": ["check", "balance", "airtime"],
     "text": "Dial *125# to check your airtime and bundle balances.",
     "effective_from": date(2026, 1, 1), "expires": None},
    {"id": "faq-bundle-expiry", "title": "What happens when a bundle expires",
     "keywords": ["expire", "expiry", "expires", "rollover", "unused"],
     "text": "Unused bundle data does not roll over and is lost when the bundle expires.",
     "effective_from": date(2026, 1, 1), "expires": None},
    {"id": "faq-buy-for-other", "title": "Buying a bundle for someone else",
     "keywords": ["someone", "another", "gift", "friend"],
     "text": "Dial *143#, choose 'Buy for another number' and enter their number.",
     "effective_from": date(2026, 1, 1), "expires": None},
]


def current_docs(today: date | None = None) -> list[dict]:
    today = today or date.today()
    return [d for d in BUNDLES + FAQS
            if d["effective_from"] <= today and (d["expires"] is None or today <= d["expires"])]
