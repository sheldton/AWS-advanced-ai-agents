"""
AnyCompany Bank — synthetic "systems of record" shared by every MLADAS demo notebook.

Everything in this file is FICTIONAL and ILLUSTRATIVE: customers, balances, rates,
policies and bureau data are invented for teaching. Nothing here is financial advice.

The functions below play the role of the bank's back-end APIs (core banking, card
processor, loan origination system, case management). Notebooks wrap them as Strands
@tool functions so that each specialist agent only receives the APIs it needs
(least privilege: Module 1, slide 10).
"""

from __future__ import annotations

import copy
import datetime as _dt
import itertools
from typing import Any

TODAY = _dt.date(2026, 9, 26)          # fixed "today" so demo output is reproducible
BANK_NAME = "AnyCompany Bank"

# --------------------------------------------------------------------------------------
# Customers & accounts (core banking)
# --------------------------------------------------------------------------------------
CUSTOMERS: dict[str, dict[str, Any]] = {
    "CUST-1001": {
        "name": "Sofia Martinez", "segment": "Premier", "customer_since": "2017-04-12",
        "preferred_channel": "email", "language": "en", "city": "Austin, TX",
        "bureau_ref": "BR-77410", "card_last4": "4417",
    },
    "CUST-1002": {
        "name": "James Chen", "segment": "Everyday", "customer_since": "2021-11-03",
        "preferred_channel": "sms", "language": "en", "city": "Seattle, WA",
        "bureau_ref": "BR-55102", "card_last4": "7731",
    },
    "CUST-1003": {
        "name": "Aisha Patel", "segment": "Everyday", "customer_since": "2025-06-20",
        "preferred_channel": "app", "language": "en", "city": "Chicago, IL",
        "bureau_ref": "BR-90315", "card_last4": "2208",
    },
}

ACCOUNTS: dict[str, list[dict[str, Any]]] = {
    "CUST-1001": [
        {"account_id": "CHK-1001-01", "type": "checking", "balance": 8_412.37, "currency": "USD"},
        {"account_id": "SAV-1001-02", "type": "savings", "balance": 31_950.00, "currency": "USD"},
    ],
    "CUST-1002": [
        {"account_id": "CHK-1002-01", "type": "checking", "balance": 2_140.88, "currency": "USD"},
    ],
    "CUST-1003": [
        {"account_id": "CHK-1003-01", "type": "checking", "balance": 1_205.10, "currency": "USD"},
        {"account_id": "SAV-1003-02", "type": "savings", "balance": 4_300.00, "currency": "USD"},
    ],
}

# --------------------------------------------------------------------------------------
# Card processor
# --------------------------------------------------------------------------------------
CARD_TRANSACTIONS: dict[str, list[dict[str, Any]]] = {
    # Sofia: a duplicate charge -> billing dispute storyline
    "4417": [
        {"txn_id": "T-88120", "date": "2026-09-14", "merchant": "Whole Foods Market", "amount": 142.18, "country": "US", "channel": "card_present"},
        {"txn_id": "T-88341", "date": "2026-09-18", "merchant": "Harbor Electronics", "amount": 489.99, "country": "US", "channel": "online"},
        {"txn_id": "T-88342", "date": "2026-09-18", "merchant": "Harbor Electronics", "amount": 489.99, "country": "US", "channel": "online"},
        {"txn_id": "T-88590", "date": "2026-09-21", "merchant": "Austin Energy", "amount": 96.40, "country": "US", "channel": "recurring"},
    ],
    # James: card-not-present burst across three countries -> fraud swarm storyline
    "7731": [
        {"txn_id": "T-90011", "date": "2026-09-25", "time": "21:02", "merchant": "Blue Bottle Coffee", "amount": 6.75, "country": "US", "channel": "card_present"},
        {"txn_id": "T-90102", "date": "2026-09-26", "time": "02:14", "merchant": "ELEC-SHOP.RO", "amount": 1_249.00, "country": "RO", "channel": "card_not_present"},
        {"txn_id": "T-90103", "date": "2026-09-26", "time": "02:21", "merchant": "GIFTCARDS-SG", "amount": 500.00, "country": "SG", "channel": "card_not_present"},
        {"txn_id": "T-90104", "date": "2026-09-26", "time": "02:33", "merchant": "CRYPTO-XCHG", "amount": 2_000.00, "country": "SG", "channel": "card_not_present"},
    ],
    "2208": [
        {"txn_id": "T-70001", "date": "2026-09-20", "merchant": "CTA Transit", "amount": 5.00, "country": "US", "channel": "card_present"},
    ],
}

CARD_STATUS: dict[str, str] = {"4417": "active", "7731": "active", "2208": "active"}

SECURITY_EVENTS: dict[str, list[dict[str, Any]]] = {
    "CUST-1002": [
        {"ts": "2026-09-25T23:41Z", "event": "login", "device": "new-android-device", "ip_country": "RO", "mfa": "sms_otp_passed"},
        {"ts": "2026-09-25T23:44Z", "event": "profile_change", "detail": "phone number changed", "ip_country": "RO"},
        {"ts": "2026-09-26T02:10Z", "event": "travel_notice_check", "detail": "no travel notice on file"},
    ],
}

# --------------------------------------------------------------------------------------
# Lending (loan origination system)
# --------------------------------------------------------------------------------------
LOAN_PRODUCTS: dict[str, dict[str, Any]] = {
    "personal":         {"apr_range": [9.49, 15.99], "amount_range": [2_000, 50_000],    "term_months": [12, 60],  "secured": False},
    "home_improvement": {"apr_range": [7.99, 12.49], "amount_range": [5_000, 100_000],   "term_months": [24, 120], "secured": False},
    "mortgage_30y":     {"apr_range": [6.125, 6.875], "amount_range": [80_000, 1_500_000], "term_months": [360, 360], "secured": True},
}

LENDING_POLICY: dict[str, Any] = {
    "version": "LP-2026.3",
    "min_credit_score": {"personal": 660, "home_improvement": 660, "mortgage_30y": 620},
    "max_dti": 0.43,                     # debt-to-income incl. the new payment
    "manual_review_if": [
        "credit score within 20 points of the product minimum",
        "thin credit file (fewer than 3 open tradelines)",
        "DTI between 0.40 and 0.43",
    ],
    "required_documents": ["pay_stub", "bank_statement", "government_id"],
}

LOAN_APPLICATIONS: dict[str, dict[str, Any]] = {
    "APP-2001": {"customer_id": "CUST-1001", "product": "home_improvement", "amount": 25_000, "term_months": 60,
                 "stated_annual_income": 96_000, "status": "documents_received", "submitted": "2026-09-22"},
    "APP-2002": {"customer_id": "CUST-1002", "product": "mortgage_30y", "amount": 420_000, "term_months": 360,
                 "stated_annual_income": 138_000, "status": "in_underwriting", "submitted": "2026-09-10"},
    "APP-2003": {"customer_id": "CUST-1003", "product": "personal", "amount": 8_000, "term_months": 36,
                 "stated_annual_income": 52_000, "status": "documents_received", "submitted": "2026-09-24"},
}

# Unstructured documents — input for the reusable document-extraction agent
DOCUMENTS: dict[str, dict[str, str]] = {
    "APP-2001": {
        "pay_stub": (
            "BRIGHTWAVE SOLAR LLC — EARNINGS STATEMENT\nEmployee: Sofia Martinez   Pay period: 09/01/2026-09/15/2026\n"
            "Gross pay this period: $4,000.00   YTD gross: $68,000.00   Pay frequency: semi-monthly\n"
            "Deductions: Fed tax $610.00, State tax $0.00, 401k $240.00, Medical $95.00\nNet pay: $3,055.00"
        ),
        "bank_statement": (
            "AnyCompany Bank — Checking CHK-1001-01 — Statement 08/01/2026-08/31/2026\n"
            "Opening balance $7,904.11 | Closing balance $8,120.52 | Average daily balance $7,988.40\n"
            "Recurring debits: AUTO LOAN - CARFIN $412.00; STUDENT LOAN - EDUSERV $230.00; RENT - OAKVIEW APTS $1,850.00\n"
            "Payroll credits: BRIGHTWAVE SOLAR x2 totalling $6,110.00"
        ),
        "government_id": "Texas Driver License — Name: SOFIA MARTINEZ — DOB: 1989-**-** — Expires: 2029-03-14 — Status: valid",
    },
    "APP-2003": {
        "pay_stub": (
            "LAKESIDE LOGISTICS INC — PAYSTUB\nEmployee: Aisha Patel   Period: 09/08/2026-09/21/2026 (bi-weekly)\n"
            "Gross: $2,000.00  YTD: $12,000.00 (start date 2026-06-01)\nNet: $1,561.20"
        ),
        "bank_statement": (
            "AnyCompany Bank — Checking CHK-1003-01 — Statement 08/01/2026-08/31/2026\n"
            "Opening $980.44 | Closing $1,105.87 | Avg daily $1,020.13\nRecurring debits: RENT - LOOP LOFTS $1,150.00; PHONE $65.00"
        ),
        "government_id": "Illinois State ID — Name: AISHA PATEL — Expires: 2031-01-09 — Status: valid",
    },
}

# Partner data: in production this lives at the CREDIT BUREAU (a different company).
# The M01 notebook deploys a partner "credit bureau agent" on AgentCore Runtime (A2A)
# that owns this data; the copy here is only the bank's cached fallback (graceful degradation).
CREDIT_BUREAU_CACHE: dict[str, dict[str, Any]] = {
    "BR-77410": {"score": 742, "open_tradelines": 6, "delinquencies_24m": 0, "utilization": 0.18, "inquiries_6m": 1, "as_of": "2026-08-31"},
    "BR-55102": {"score": 688, "open_tradelines": 4, "delinquencies_24m": 1, "utilization": 0.41, "inquiries_6m": 3, "as_of": "2026-08-31"},
    "BR-90315": {"score": 671, "open_tradelines": 2, "delinquencies_24m": 0, "utilization": 0.09, "inquiries_6m": 1, "as_of": "2026-08-31"},
}

# --------------------------------------------------------------------------------------
# Disputes & fraud (case management) — the bank's system of record for cases
# --------------------------------------------------------------------------------------
DISPUTE_POLICY: dict[str, Any] = {
    "version": "DP-2026.2",
    "filing_window_days": 60,
    "duplicate_charge": "Provisional credit within 10 business days while the merchant is contacted.",
    "unauthorized_charge": "Block the card immediately, reissue, and credit confirmed fraudulent amounts.",
    "customer_liability_unauthorized_usd": 0,
}

_case_counter = itertools.count(5001)
CASES: dict[str, dict[str, Any]] = {}   # mutated by open_case(); reset with reset_state()


# --------------------------------------------------------------------------------------
# "Back-end API" functions — notebooks wrap these as agent tools
# --------------------------------------------------------------------------------------
def get_customer_profile(customer_id: str) -> dict[str, Any]:
    """Look up a customer's profile: name, segment, preferred contact channel, card and bureau reference.

    Args:
        customer_id: Bank customer id: "CUST-" followed by 4 digits, e.g. "CUST-0000".
    """
    c = CUSTOMERS.get(customer_id)
    return {"customer_id": customer_id, **c} if c else {"error": f"unknown customer {customer_id}"}


def get_account_balances(customer_id: str) -> dict[str, Any]:
    """Current balances of all deposit accounts (checking, savings) of a customer.

    Args:
        customer_id: Bank customer id: "CUST-" followed by 4 digits, e.g. "CUST-0000".
    """
    if customer_id not in ACCOUNTS:
        return {"error": f"unknown customer {customer_id}"}
    return {"customer_id": customer_id, "as_of": str(TODAY), "accounts": ACCOUNTS[customer_id]}


def list_card_transactions(card_last4: str) -> dict[str, Any]:
    """Recent transactions and current status (active/blocked) of a card.

    Args:
        card_last4: Last four digits of the card (4 digits, e.g. "0000").
    """
    if card_last4 not in CARD_TRANSACTIONS:
        return {"error": f"unknown card ending {card_last4}"}
    return {"card_last4": card_last4, "status": CARD_STATUS[card_last4], "transactions": CARD_TRANSACTIONS[card_last4]}


def find_duplicate_charges(card_last4: str) -> dict[str, Any]:
    """Detect duplicate charges (same date, merchant and amount) on a card.

    Args:
        card_last4: Last four digits of the card (4 digits, e.g. "0000").
    """
    txns = CARD_TRANSACTIONS.get(card_last4, [])
    seen, dups = {}, []
    for t in txns:
        key = (t["date"], t["merchant"], t["amount"])
        if key in seen:
            dups.append({"original": seen[key], "duplicate": t["txn_id"], "merchant": t["merchant"], "amount": t["amount"], "date": t["date"]})
        else:
            seen[key] = t["txn_id"]
    return {"card_last4": card_last4, "duplicates": dups}


def get_security_events(customer_id: str) -> dict[str, Any]:
    """Recent login / device / profile-change security events for a customer.

    Args:
        customer_id: Bank customer id: "CUST-" followed by 4 digits, e.g. "CUST-0000".
    """
    return {"customer_id": customer_id, "events": SECURITY_EVENTS.get(customer_id, [])}


def block_card(card_last4: str, reason: str) -> dict[str, Any]:
    """Block a card immediately (irreversible in this demo) and schedule a replacement.

    Args:
        card_last4: Last four digits of the card to block.
        reason: Short reason recorded on the card, e.g. "suspected fraud".
    """
    if card_last4 not in CARD_STATUS:
        return {"error": f"unknown card ending {card_last4}"}
    CARD_STATUS[card_last4] = "blocked"
    return {"card_last4": card_last4, "status": "blocked", "reason": reason, "reissue_eta_days": 5}


def open_case(customer_id: str, case_type: str, summary: str, amount: float | None = None) -> dict[str, Any]:
    """Open a case in the bank's case-management system (the system of record for disputes and fraud).

    Args:
        customer_id: Bank customer id.
        case_type: One of "billing_dispute", "fraud", "complaint".
        summary: One-sentence description of the case.
        amount: Disputed amount in USD, if any.
    """
    case_id = f"CASE-{next(_case_counter)}"
    CASES[case_id] = {"case_id": case_id, "customer_id": customer_id, "type": case_type, "summary": summary,
                      "amount": amount, "status": "open", "opened": str(TODAY)}
    return CASES[case_id]


def get_dispute_policy() -> dict[str, Any]:
    """The bank's card dispute policy (filing window, provisional credit, fraud handling)."""
    return DISPUTE_POLICY


def get_loan_products() -> dict[str, Any]:
    """Loan products with APR ranges, amount ranges and term ranges (months)."""
    return {"products": LOAN_PRODUCTS, "note": "Illustrative rates for a fictional bank."}


def get_lending_policy() -> dict[str, Any]:
    """The bank's lending policy: minimum credit scores, maximum DTI, manual-review triggers, required documents."""
    return LENDING_POLICY


def get_application(application_id: str) -> dict[str, Any]:
    """Status and details of a loan application.

    Args:
        application_id: Loan application id: "APP-" followed by 4 digits, e.g. "APP-0000".
    """
    a = LOAN_APPLICATIONS.get(application_id)
    return {"application_id": application_id, **a} if a else {"error": f"unknown application {application_id}"}


def get_application_documents(application_id: str) -> dict[str, Any]:
    """Raw text of the documents attached to a loan application (pay stub, bank statement, government id).

    Args:
        application_id: Loan application id: "APP-" followed by 4 digits, e.g. "APP-0000".
    """
    d = DOCUMENTS.get(application_id)
    return {"application_id": application_id, "documents": d} if d else {"error": f"no documents for {application_id}"}


def monthly_payment(principal: float, apr_pct: float, term_months: int) -> float:
    """Monthly payment of an amortised loan (deterministic math — never let an LLM do arithmetic like this).

    Args:
        principal: Loan amount in USD.
        apr_pct: Annual percentage rate, e.g. 7.99.
        term_months: Term in months, e.g. 60.
    """
    r = apr_pct / 100 / 12
    if r == 0:
        return round(principal / term_months, 2)
    return round(principal * r / (1 - (1 + r) ** -term_months), 2)


# Every back-end API above, in one list (wrap with strands.tool(fn) to expose one to an agent)
BACKEND_APIS = [get_customer_profile, get_account_balances, list_card_transactions, find_duplicate_charges,
                get_security_events, block_card, open_case, get_dispute_policy, get_loan_products,
                get_lending_policy, get_application, get_application_documents, monthly_payment]


# --------------------------------------------------------------------------------------
# A small labelled evaluation set (used to compare single- vs multi-agent designs; reused in M04)
#   domains: which specialist(s) should handle it · must_include: facts a correct answer contains
# --------------------------------------------------------------------------------------
EVAL_QUESTIONS: list[dict[str, Any]] = [
    {"id": "ACC-1", "customer_id": "CUST-1001", "domains": ["accounts"],
     "question": "What's the balance in my checking account?", "must_include": ["8,412"]},
    {"id": "CRD-1", "customer_id": "CUST-1001", "domains": ["cards"],
     "question": "I think I was charged twice at Harbor Electronics last week. Can you check and open a dispute?",
     "must_include": ["489.99", "CASE-"]},
    {"id": "LND-1", "customer_id": "CUST-1001", "domains": ["lending"],
     "question": "What's the APR range and the longest term for a home improvement loan?",
     "must_include": ["7.99", "12.49", "120"]},
    {"id": "LND-2", "customer_id": "CUST-1001", "domains": ["lending"],
     "question": "Where is my loan application APP-2001 in the process?", "must_include": ["document"]},
    {"id": "X-1", "customer_id": "CUST-1001", "domains": ["cards", "lending"],
     "question": ("I was double charged at Harbor Electronics. Separately, what would my monthly payment be if I "
                  "borrow 25,000 over 60 months for home improvements at the lowest advertised rate?"),
     "must_include": ["489.99", "506.79"]},
    {"id": "CRD-2", "customer_id": "CUST-1002", "domains": ["cards"],
     "question": "There are charges on my card from Romania and Singapore that I did NOT make. Please stop them!",
     "must_include": ["block"]},
    {"id": "ACC-2", "customer_id": "CUST-1003", "domains": ["accounts"],
     "question": "How much money do I have in savings?", "must_include": ["4,300"]},
    {"id": "X-2", "customer_id": "CUST-1003", "domains": ["accounts", "lending"],
     "question": "What's the minimum credit score for a personal loan, and what's my checking balance right now?",
     "must_include": ["660", "1,205"]},
]


def customer_context(customer_id: str) -> str:
    """One-line context header a channel (web/app) would attach for a signed-in customer."""
    c = CUSTOMERS[customer_id]
    return (f"[Signed-in customer: {customer_id} · {c['name']} · card ending {c['card_last4']} · "
            f"prefers {c['preferred_channel']}]")


def score_answer(answer: str, must_include: list[str]) -> float:
    """Fraction of required facts present (normalises '8412' vs '8,412' and '$')."""
    norm = answer.replace(",", "").replace("$", "").lower()
    hits = sum(1 for m in must_include if m.replace(",", "").replace("$", "").lower() in norm)
    return hits / len(must_include) if must_include else 1.0


def reset_state() -> None:
    """Restore mutable demo state (card status, cases) so notebook sections can be re-run."""
    global _case_counter
    for k in CARD_STATUS:
        CARD_STATUS[k] = "active"
    CASES.clear()
    _case_counter = itertools.count(5001)


def snapshot() -> dict[str, Any]:
    """Deep copy of mutable state — handy for showing what agents changed."""
    return copy.deepcopy({"card_status": CARD_STATUS, "cases": CASES})


# ======================================================================================
# M02 — Context Engineering additions (appended; nothing above this banner changes)
#   A static service handbook (the cached prefix), raw logs from three bank services,
#   uniform transaction histories, a raw 40-field core-banking record, a large CSV for the
#   sandbox, and last week's chat sessions for memory. Everything is deterministic:
#   seeded random.Random, fixed dates, no wall clock, no file or network I/O at import.
# ======================================================================================
import bisect as _bisect  # noqa: E402
import csv as _csv  # noqa: E402
import io as _io  # noqa: E402
import random as _random  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

# --------------------------------------------------------------------------------------
# Customer Service Handbook — static reference document (the cached prefix in M02 §5)
# --------------------------------------------------------------------------------------
HANDBOOK_VERSION = "SH-2026.3"
_HANDBOOK_PATH = _Path(__file__).resolve().parent / "bank_docs" / "service_handbook.md"


def load_handbook() -> str:
    """The Customer Service Handbook SH-2026.3 as markdown (static text; read from bank_docs/)."""
    return _HANDBOOK_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# Merchant catalog (fictional) shared by the transaction generators below
#   name: (mcc, category, country, channel, min_amount_usd, max_amount_usd)
# --------------------------------------------------------------------------------------
_MERCHANT_CATALOG: dict[str, tuple[str, str, str, str, float, float]] = {
    "Greenleaf Grocers":       ("5411", "groceries", "US", "card_present", 18.0, 210.0),
    "Riverside Market":        ("5411", "groceries", "US", "card_present", 12.0, 160.0),
    "Cedar Grill":             ("5812", "restaurants", "US", "card_present", 24.0, 140.0),
    "Lotus Noodle Bar":        ("5812", "restaurants", "US", "card_present", 14.0, 70.0),
    "Daybreak Coffee":         ("5814", "coffee_fast_food", "US", "card_present", 3.5, 14.0),
    "Northstar Fuel":          ("5541", "fuel", "US", "card_present", 28.0, 82.0),
    "Wellspring Pharmacy":     ("5912", "pharmacy", "US", "card_present", 6.0, 95.0),
    "Buildright Home Center":  ("5200", "home_improvement", "US", "card_present", 22.0, 640.0),
    "Parcelio Marketplace":    ("5999", "online_retail", "US", "online", 9.0, 260.0),
    "Voltline Electronics":    ("5732", "electronics", "US", "online", 35.0, 1150.0),
    "StreamNest":              ("4899", "subscriptions", "US", "recurring", 15.99, 15.99),
    "TuneBox Music":           ("4899", "subscriptions", "US", "recurring", 10.99, 10.99),
    "Peak Fitness Club":       ("7997", "fitness", "US", "recurring", 49.0, 49.0),
    "GameVault Online":        ("5816", "digital_games", "US", "recurring", 14.99, 59.99),
    "RideLoop":                ("4121", "rideshare", "US", "online", 8.0, 46.0),
    "Metro Transit":           ("4111", "transit", "US", "card_present", 2.5, 5.0),
    "Northwind Airways":       ("4511", "airlines", "US", "card_not_present", 180.0, 920.0),
    "Petal and Stem Florist":  ("5992", "florist", "US", "card_not_present", 45.0, 160.0),
    "Lakeside Inn and Suites": ("7011", "hotels", "US", "card_not_present", 140.0, 610.0),
    "Mercado Oaxaca":          ("5999", "retail", "MX", "card_present", 10.0, 180.0),
    "Playa Azul Resort":       ("7011", "hotels", "MX", "card_present", 220.0, 880.0),
    "Cafe Pacifico":           ("5812", "restaurants", "MX", "card_present", 12.0, 95.0),
    "Maple Leaf Books":        ("5942", "books", "CA", "card_present", 12.0, 70.0),
    "Granville Diner":         ("5812", "restaurants", "CA", "card_present", 15.0, 85.0),
}

# mcc / category for the merchants that appear in CARD_TRANSACTIONS (the real rows)
_REAL_MERCHANT_META: dict[str, tuple[str, str]] = {
    "Whole Foods Market": ("5411", "groceries"), "Harbor Electronics": ("5732", "electronics"),
    "Austin Energy": ("4900", "utilities"), "Blue Bottle Coffee": ("5814", "coffee_fast_food"),
    "ELEC-SHOP.RO": ("5732", "electronics"), "GIFTCARDS-SG": ("5947", "gift_cards"),
    "CRYPTO-XCHG": ("6051", "crypto_quasi_cash"), "CTA Transit": ("4111", "transit"),
}
# CARD_TRANSACTIONS rows without a time get a fixed one (UTC, HH:MM)
_REAL_TXN_TIMES: dict[str, str] = {"T-88120": "18:42", "T-88341": "14:03", "T-88342": "14:03",
                                   "T-88590": "06:00", "T-70001": "08:17"}

_HOUR_WEIGHTS = [1, 1, 0.5, 0.5, 0.5, 1, 2, 4, 6, 6, 6, 7, 9, 8, 7, 7, 8, 9, 10, 9, 7, 5, 3, 2]
_HOUR_CUM = list(itertools.accumulate(_HOUR_WEIGHTS))

# Per-card spending profiles for the generated history (weights are relative)
_CARD_PROFILES: dict[str, dict[str, Any]] = {
    "4417": {  # Sofia, Austin TX — Premier; a July trip to Mexico
        "days": 90, "decline_rate": 0.10, "decline_codes": ["N7", "05", "54", "61", "91", "65"],
        "merchants": {"Greenleaf Grocers": 10, "Riverside Market": 5, "Cedar Grill": 5, "Daybreak Coffee": 7,
                      "Northstar Fuel": 5, "Wellspring Pharmacy": 3, "Buildright Home Center": 4,
                      "Parcelio Marketplace": 6, "Voltline Electronics": 1, "StreamNest": 1, "Peak Fitness Club": 1,
                      "Petal and Stem Florist": 2, "Lakeside Inn and Suites": 1, "Northwind Airways": 1},
        "trip": ("2026-07-09", "2026-07-15", {"Mercado Oaxaca": 3, "Playa Azul Resort": 2, "Cafe Pacifico": 3}),
    },
    "7731": {  # James, Seattle WA — Everyday; a long weekend in Canada
        "days": 90, "decline_rate": 0.12, "decline_codes": ["51", "51", "05", "N7", "65"],
        "merchants": {"Riverside Market": 7, "Lotus Noodle Bar": 6, "Daybreak Coffee": 9, "RideLoop": 6,
                      "Metro Transit": 6, "Parcelio Marketplace": 6, "Voltline Electronics": 2, "GameVault Online": 4,
                      "TuneBox Music": 1, "StreamNest": 1, "Northstar Fuel": 3, "Wellspring Pharmacy": 2},
        "trip": ("2026-08-21", "2026-08-24", {"Maple Leaf Books": 2, "Granville Diner": 3}),
    },
    "2208": {  # Aisha, Chicago IL — Everyday
        "days": 90, "decline_rate": 0.08, "decline_codes": ["51", "05", "N7", "54"],
        "merchants": {"Metro Transit": 10, "Greenleaf Grocers": 6, "Lotus Noodle Bar": 4, "Daybreak Coffee": 7,
                      "Parcelio Marketplace": 5, "RideLoop": 4, "TuneBox Music": 1, "Wellspring Pharmacy": 2,
                      "Cedar Grill": 2, "Peak Fitness Club": 1},
        "trip": None,
    },
}

_TXN_KEYS = ("txn_id", "date", "time", "merchant", "mcc", "category", "amount", "currency", "country",
             "channel", "status", "decline_code")


def _pick(rng: _random.Random, weights: dict[str, float]) -> str:
    names = list(weights)
    return rng.choices(names, weights=[weights[k] for k in names])[0]


def _txn_row(txn_id: str, date: str, time: str, merchant: str, amount: float, country: str, channel: str,
             status: str = "approved", decline_code: str = "") -> dict[str, Any]:
    mcc, category = _REAL_MERCHANT_META.get(merchant) or _MERCHANT_CATALOG[merchant][:2]
    return dict(zip(_TXN_KEYS, (txn_id, date, time, merchant, mcc, category, round(amount, 2), "USD", country,
                                channel, status, decline_code)))


def transaction_history(card_last4: str, n: int = 60, seed: int = 7) -> list[dict[str, Any]]:
    """A card's recent transaction history: n uniform rows (same keys, same order), oldest first.

    The real CARD_TRANSACTIONS rows for the card are the most recent rows (at the end); the earlier rows are
    generated deterministically from `seed`.

    Args:
        card_last4: Last four digits of the card, e.g. "4417" (Sofia) or "7731" (James).
        n: Total number of rows, real rows included.
        seed: Seed for the generated rows.
    """
    if card_last4 not in CARD_TRANSACTIONS or card_last4 not in _CARD_PROFILES:
        raise ValueError(f"unknown card ending {card_last4}")
    real = [_txn_row(t["txn_id"], t["date"], t.get("time") or _REAL_TXN_TIMES[t["txn_id"]], t["merchant"],
                     t["amount"], t["country"], t["channel"]) for t in CARD_TRANSACTIONS[card_last4]]
    real.sort(key=lambda r: (r["date"], r["time"]))
    prof = _CARD_PROFILES[card_last4]
    rng = _random.Random(seed * 10_000 + int(card_last4))
    start = _dt.date.fromisoformat(real[0]["date"]) - _dt.timedelta(days=prof["days"])
    stamps = sorted((rng.randrange(prof["days"]), _bisect.bisect(_HOUR_CUM, rng.random() * _HOUR_CUM[-1]),
                     rng.randrange(60)) for _ in range(max(0, n - len(real))))
    first_id = int(real[0]["txn_id"].split("-")[1]) - 1000
    rows = []
    for i, (day, hour, minute) in enumerate(stamps):
        date = start + _dt.timedelta(days=day)
        trip = prof["trip"]
        if trip and trip[0] <= date.isoformat() <= trip[1] and rng.random() < 0.8:
            merchant = _pick(rng, trip[2])
        else:
            merchant = _pick(rng, prof["merchants"])
        _mcc, _cat, country, channel, lo, hi = _MERCHANT_CATALOG[merchant]
        amount = lo if lo == hi else rng.uniform(lo, hi)
        declined = rng.random() < prof["decline_rate"]
        rows.append(_txn_row(f"T-{first_id + i}", date.isoformat(), f"{hour:02d}:{minute:02d}", merchant, amount,
                             country, channel, "declined" if declined else "approved",
                             rng.choice(prof["decline_codes"]) if declined else ""))
    return (rows + real)[-n:] if n > 0 else []


def _date_forms(iso: str) -> list[str]:
    d = _dt.date.fromisoformat(iso)
    return [iso, f"{d:%B} {d.day}", f"{d:%b} {d.day}", f"{d.day} {d:%B}", f"{d.month}/{d.day}"]


def _money_forms(x: float) -> list[str]:
    return [f"{x:,.2f}", f"{x:.2f}"]


_NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
                 "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]


def _count_forms(k: int) -> list[str]:
    return [str(k)] + ([_NUMBER_WORDS[k]] if k < len(_NUMBER_WORDS) else [])


def _build_txn_questions(card_last4: str = "4417") -> list[dict[str, Any]]:
    """Five questions over transaction_history(card_last4); every answer is computed from the rows."""
    rows = transaction_history(card_last4)
    approved = [r for r in rows if r["status"] == "approved"]
    n_declined = sum(1 for r in rows if r["status"] == "declined")
    freq: dict[str, int] = {}
    for r in approved:
        if r["channel"] != "recurring":
            freq[r["merchant"]] = freq.get(r["merchant"], 0) + 1
    merchant = sorted(freq, key=lambda m: (-freq[m], m))[0]
    merchant_total = round(sum(r["amount"] for r in approved if r["merchant"] == merchant), 2)
    biggest = max(approved, key=lambda r: (r["amount"], r["date"], r["time"]))
    countries = sorted({r["country"] for r in rows})
    cnp = min((r for r in rows if r["channel"] == "card_not_present"), key=lambda r: (r["date"], r["time"]))
    base = {"card_last4": card_last4, "n_rows": len(rows)}
    return [
        {"id": "TXN-1", **base, "question": "How many transactions in this history were declined?",
         "answer": n_declined, "accept": _count_forms(n_declined)},
        {"id": "TXN-2", **base, "question": f"What is the total amount of the approved transactions at {merchant}?",
         "answer": merchant_total, "accept": _money_forms(merchant_total)},
        {"id": "TXN-3", **base, "question": "On what date was the largest single approved charge?",
         "answer": biggest["date"], "accept": _date_forms(biggest["date"])},
        {"id": "TXN-4", **base, "question": "In how many distinct countries did transactions take place "
                                           "(approved or declined)?",
         "answer": len(countries), "accept": _count_forms(len(countries))},
        {"id": "TXN-5", **base, "question": "Which merchant had the earliest transaction with channel "
                                           "card_not_present?",
         "answer": cnp["merchant"], "accept": [cnp["merchant"]]},
    ]


# Ground-truth questions for the 60-row history of Sofia's card 4417 (answers computed, never hard-coded)
TXN_QUESTIONS: list[dict[str, Any]] = _build_txn_questions("4417")


# --------------------------------------------------------------------------------------
# Raw service logs around James Chen's fraud burst (card 7731) — card-auth, fraud-engine,
# notification-service, 2026-09-25T20:00Z .. 2026-09-26T04:00Z. Planted facts: LOG_FACTS.
# --------------------------------------------------------------------------------------
LOG_FACTS: dict[str, Any] = {
    "card_last4": "7731",
    "customer_id": "CUST-1002",
    "window": ["2026-09-25T20:00Z", "2026-09-26T04:00Z"],
    "fraud_txn_ids": ["T-90102", "T-90103", "T-90104"],
    "fraud_txn_decision": "APPROVED",           # all three card-not-present transactions (card-auth)
    "fraud_total_usd": 3749.00,
    "alert_id": "FRD-77310",                    # fraud-engine
    "alert_time": "2026-09-26T02:35Z",
    "alert_score": 0.93,
    "alert_rule": "CNP_VELOCITY_3_COUNTRIES",
    "alert_auto_block": False,
    "phone_change_time": "2026-09-25T23:44Z",   # notification-service contact registry
    "new_phone_last4": "0199",                  # set by the attacker
    "verified_phone_last4": "4402",             # verified phone on file before the change
    "sms_alert_time": "2026-09-26T02:36Z",
    "sms_alert_to_last4": "0199",
    "sms_alert_status": "DELIVERED",
    "push_alert_status": "FAILED",
    "push_error": "device token expired",       # the only ERROR about push
}

_LOG_T0 = _dt.datetime(2026, 9, 25, 20, 0, 0)
_LOG_SPAN_MS = 8 * 3600 * 1000
_LOG_RESERVED_4 = {"7731", "4417", "2208", "0199", "4402", "5580", "0158", "3c91"}
# another customer's (unrelated, correctly delivered) ATM alert that appears in two services
_OTHER_ALERT = {"alert_id": "FRD-77264", "card": "****5580", "customer": "CUST-2614", "phone": "+1******0158"}
_LOG_NOISE = {"card-auth": 50, "fraud-engine": 58, "notification-service": 44}   # noise events per service
_ENTRY_MODE = {"card_present": "chip", "online": "ecommerce", "recurring": "card_on_file", "card_not_present": "moto"}


def _log(ms: int, level: str, service: str, event: str, fields: dict[str, Any]) -> str:
    """One structured log line; `ms` is milliseconds after 2026-09-25T20:00:00Z."""
    t = _LOG_T0 + _dt.timedelta(milliseconds=ms)
    kv = " ".join(f'{k}="{v}"' if isinstance(v, str) and (" " in v or not v) else f"{k}={v}" for k, v in fields.items())
    return f"{t:%Y-%m-%dT%H:%M:%S}.{t.microsecond // 1000:03d}Z {level:<5} {service} {event} {kv}"


def _at(hms: str, day: int = 26) -> int:
    """'02:14:07.418' on 2026-09-<day> -> milliseconds after the log window start."""
    h, m, s = hms.split(":")
    t = _dt.datetime(2026, 9, day, int(h), int(m)) + _dt.timedelta(seconds=float(s))
    return int((t - _LOG_T0).total_seconds() * 1000)


def _other4(rng: _random.Random) -> str:
    while True:
        s = f"{rng.randrange(10_000):04d}"
        if s not in _LOG_RESERVED_4:
            return s


def _noise_times(rng: _random.Random, k: int) -> list[int]:
    return sorted(rng.randrange(_LOG_SPAN_MS) for _ in range(k))


def _noise_auth(rng: _random.Random) -> tuple[dict[str, Any], str, float]:
    """A random other-customer authorization: fields, merchant channel, fraud score."""
    merchant = rng.choice(list(_MERCHANT_CATALOG))
    mcc, _cat, country, channel, lo, hi = _MERCHANT_CATALOG[merchant]
    amount = lo if lo == hi else rng.uniform(lo, hi)
    return ({"txn": f"T-{rng.randint(600_000, 689_999)}", "card": f"****{_other4(rng)}", "merchant": merchant,
             "mcc": mcc, "amount": f"{amount:.2f}", "currency": "USD", "country": country, "channel": channel},
            channel, round(rng.uniform(0.01, 0.31), 2))


def _card_auth_log(rng: _random.Random) -> list[str]:
    svc, lines = "card-auth", []
    for ms in _noise_times(rng, _LOG_NOISE["card-auth"]):
        pod = f"card-auth-{rng.randint(1, 3)}"
        kind = rng.choices(["approve", "decline", "health", "echo", "hsm"], weights=[46, 8, 12, 8, 4])[0]
        if kind in ("approve", "decline"):
            f, channel, score = _noise_auth(rng)
            ok = kind == "approve"
            f.update({"entry": _ENTRY_MODE[channel], "decision": "APPROVED" if ok else "DECLINED",
                      "resp_code": "00" if ok else rng.choice(["51", "05", "N7", "54", "61", "65", "91"]),
                      "fraud_score": score, "latency_ms": rng.randint(35, 180)})
            lines.append(_log(ms, "INFO", svc, "auth.decision", {"pod": pod, **f}))
        elif kind == "health":
            lines.append(_log(ms, "DEBUG", svc, "health.check", {"pod": pod, "status": "ok",
                                                                 "upstream": "cardnet-a,cardnet-b,hsm,ledger",
                                                                 "latency_ms": rng.randint(2, 9)}))
        elif kind == "echo":
            lines.append(_log(ms, "INFO", svc, "network.echo", {"pod": pod, "network": rng.choice(["CARDNET-A", "CARDNET-B"]),
                                                                "status": "UP", "rtt_ms": rng.randint(18, 95)}))
        else:
            lines.append(_log(ms, "DEBUG", svc, "hsm.keycheck", {"pod": pod, "slot": rng.randint(1, 4), "kcv": "OK"}))
    for hhmm, day in (("22:00:00.004", 25), ("00:00:00.007", 26), ("02:00:00.003", 26)):
        lines.append(_log(_at(hhmm, day), "INFO", svc, "settlement.batch_close",
                          {"pod": "card-auth-1", "batch_id": f"SB-{day:02d}-{hhmm[:2]}", "records": rng.randint(1400, 2600),
                           "amount_usd": f"{rng.uniform(90_000, 240_000):.2f}", "status": "CLOSED"}))
    lines += [
        _log(_at("00:37:12.550"), "ERROR", svc, "tokenization.timeout",
             {"pod": "card-auth-2", "txn": f"T-{rng.randint(600_000, 689_999)}", "attempt": 1, "timeout_ms": 800,
              "outcome": "retried_ok"}),
        _log(_at("01:12:40.118"), "WARN", svc, "acquirer.latency",
             {"pod": "card-auth-3", "acquirer": "ACQ-EU-3", "p99_ms": 1840, "threshold_ms": 1500}),
        _log(_at("03:41:05.902"), "WARN", svc, "stand_in.enabled",
             {"pod": "card-auth-1", "network": "CARDNET-B", "reason": "echo_timeout", "duration_s": 38}),
        # --- planted: James's card 7731 ---
        _log(_at("21:02:31.207", 25), "INFO", svc, "auth.decision",
             {"pod": "card-auth-2", "txn": "T-90011", "card": "****7731", "merchant": "Blue Bottle Coffee", "mcc": "5814",
              "amount": "6.75", "currency": "USD", "country": "US", "channel": "card_present", "entry": "contactless",
              "decision": "APPROVED", "resp_code": "00", "fraud_score": 0.02, "latency_ms": 41}),
        _log(_at("02:14:07.418"), "INFO", svc, "auth.decision",
             {"pod": "card-auth-1", "txn": "T-90102", "card": "****7731", "merchant": "ELEC-SHOP.RO", "mcc": "5732",
              "amount": "1249.00", "currency": "USD", "country": "RO", "channel": "card_not_present",
              "entry": "ecommerce", "3ds": "not_attempted", "decision": "APPROVED", "resp_code": "00",
              "fraud_score": 0.41, "latency_ms": 212}),
        _log(_at("02:21:44.031"), "INFO", svc, "auth.decision",
             {"pod": "card-auth-3", "txn": "T-90103", "card": "****7731", "merchant": "GIFTCARDS-SG", "mcc": "5947",
              "amount": "500.00", "currency": "USD", "country": "SG", "channel": "card_not_present",
              "entry": "ecommerce", "3ds": "not_attempted", "decision": "APPROVED", "resp_code": "00",
              "fraud_score": 0.58, "latency_ms": 187}),
        _log(_at("02:33:19.660"), "INFO", svc, "auth.decision",
             {"pod": "card-auth-1", "txn": "T-90104", "card": "****7731", "merchant": "CRYPTO-XCHG", "mcc": "6051",
              "amount": "2000.00", "currency": "USD", "country": "SG", "channel": "card_not_present",
              "entry": "ecommerce", "3ds": "not_attempted", "decision": "APPROVED", "resp_code": "00",
              "fraud_score": 0.74, "latency_ms": 239}),
        _log(_at("02:33:19.702"), "WARN", svc, "velocity.advisory",
             {"pod": "card-auth-1", "card": "****7731", "cnp_count_20m": 3, "countries": "RO,SG", "action": "none",
              "note": "advisory only; decisioning is delegated to fraud-engine"}),
    ]
    return lines


def _fraud_engine_log(rng: _random.Random) -> list[str]:
    svc, lines = "fraud-engine", []
    for ms in _noise_times(rng, _LOG_NOISE["fraud-engine"]):
        worker = f"fe-worker-{rng.randint(1, 4)}"
        kind = rng.choices(["score", "features", "heartbeat", "rules"], weights=[48, 8, 10, 6])[0]
        if kind == "score":
            f, channel, score = _noise_auth(rng)
            model = "cp-gbm-v9" if channel == "card_present" else "cnp-gbm-v14"
            lines.append(_log(ms, "INFO", svc, "score.realtime",
                              {"worker": worker, "txn": f["txn"], "card": f["card"], "model": model, "score": score,
                               "threshold_decline": 0.85, "action": "allow", "latency_ms": rng.randint(4, 22)}))
        elif kind == "features":
            lines.append(_log(ms, "DEBUG", svc, "features.refresh",
                              {"worker": worker, "feature_group": rng.choice(["velocity_1h", "merchant_risk", "device_graph"]),
                               "rows_upserted": rng.randint(800, 9000), "lag_ms": rng.randint(40, 900)}))
        elif kind == "heartbeat":
            lines.append(_log(ms, "DEBUG", svc, "heartbeat", {"worker": worker, "status": "ok", "queue": "auth-events",
                                                              "depth": rng.randint(0, 40)}))
        else:
            lines.append(_log(ms, "INFO", svc, "rules.batch_eval", {"worker": worker, "ruleset": "RS-2026.09.2",
                                                                    "evaluated": rng.randint(200, 1500), "matched": 0}))
    lines += [
        _log(_at("20:00:04.215", 25), "INFO", svc, "rules.loaded",
             {"worker": "fe-worker-1", "ruleset": "RS-2026.09.2", "rules": 148, "mode": "notify_only_for_velocity"}),
        _log(_at("22:17:40.388", 25), "WARN", svc, "alert.raised",
             {"worker": "fe-worker-3", "alert_id": _OTHER_ALERT["alert_id"], "card": _OTHER_ALERT["card"],
              "customer": _OTHER_ALERT["customer"], "rule": "ATM_GEO_MISMATCH", "alert_score": 0.81,
              "action": "REVIEW_QUEUE", "auto_block": "false"}),
        _log(_at("22:17:40.412", 25), "INFO", svc, "alert.dispatch",
             {"worker": "fe-worker-3", "alert_id": _OTHER_ALERT["alert_id"], "target": "notification-service",
              "template": "FRAUD_ALERT_ATM", "channels": "sms,push", "status": "queued"}),
        _log(_at("00:05:33.020"), "WARN", svc, "model.drift",
             {"worker": "fe-worker-2", "model": "cnp-gbm-v14", "feature": "merchant_country", "psi": 0.18, "threshold": 0.20}),
        _log(_at("01:47:09.871"), "ERROR", svc, "feature_store.read_error",
             {"worker": "fe-worker-4", "feature_group": "device_graph", "error": "connection reset by peer",
              "fallback": "cached_features", "cache_age_s": 312}),
        _log(_at("03:12:26.640"), "WARN", svc, "queue.lag",
             {"worker": "fe-worker-2", "consumer": "alert-dispatch", "lag_ms": 2300, "threshold_ms": 2000}),
        # --- planted: James (CUST-1002), card 7731 ---
        _log(_at("23:41:07.550", 25), "INFO", svc, "event.ingest",
             {"worker": "fe-worker-1", "type": "login", "customer": "CUST-1002", "device": "new-android-device",
              "ip_country": "RO", "mfa": "sms_otp_passed", "risk_signal": "new_device_new_country"}),
        _log(_at("23:44:12.904", 25), "INFO", svc, "event.ingest",
             {"worker": "fe-worker-2", "type": "profile_change", "customer": "CUST-1002", "field": "phone",
              "ip_country": "RO", "risk_signal": "contact_change"}),
        _log(_at("02:10:52.117"), "INFO", svc, "context.travel_notice",
             {"worker": "fe-worker-3", "card": "****7731", "customer": "CUST-1002", "result": "none_on_file"}),
        _log(_at("02:14:07.301"), "INFO", svc, "score.realtime",
             {"worker": "fe-worker-3", "txn": "T-90102", "card": "****7731", "model": "cnp-gbm-v14", "score": 0.41,
              "threshold_decline": 0.85, "action": "allow", "latency_ms": 17}),
        _log(_at("02:21:43.925"), "INFO", svc, "score.realtime",
             {"worker": "fe-worker-1", "txn": "T-90103", "card": "****7731", "model": "cnp-gbm-v14", "score": 0.58,
              "threshold_decline": 0.85, "action": "allow", "latency_ms": 15}),
        _log(_at("02:33:19.544"), "INFO", svc, "score.realtime",
             {"worker": "fe-worker-4", "txn": "T-90104", "card": "****7731", "model": "cnp-gbm-v14", "score": 0.74,
              "threshold_decline": 0.85, "action": "allow", "latency_ms": 19}),
        _log(_at("02:35:41.006"), "WARN", svc, "alert.raised",
             {"worker": "fe-worker-4", "alert_id": "FRD-77310", "card": "****7731", "customer": "CUST-1002",
              "rule": "CNP_VELOCITY_3_COUNTRIES", "alert_score": 0.93, "txns": "T-90102,T-90103,T-90104",
              "total_usd": "3749.00", "countries": "US,RO,SG", "window": "6h", "action": "NOTIFY_CUSTOMER",
              "auto_block": "false"}),
        _log(_at("02:35:41.030"), "INFO", svc, "alert.dispatch",
             {"worker": "fe-worker-4", "alert_id": "FRD-77310", "target": "notification-service",
              "template": "FRAUD_ALERT_CNP", "channels": "sms,push", "status": "queued"}),
    ]
    return lines


def _notification_log(rng: _random.Random) -> list[str]:
    svc, lines = "notification-service", []

    def msg_id(prefix: str) -> str:
        return f"{prefix}-{rng.randrange(16 ** 6):06x}"

    for ms in _noise_times(rng, _LOG_NOISE["notification-service"]):
        pod = f"notify-{rng.randint(1, 2)}"
        cust = f"CUST-{rng.randint(2000, 4999)}"
        kind = rng.choices(["sms", "email", "push", "health", "queue"], weights=[14, 12, 10, 8, 7])[0]
        if kind == "sms":
            mid, to = msg_id("SMS"), f"+1******{_other4(rng)}"
            tpl = rng.choice(["PAYMENT_DUE", "LOW_BALANCE", "LOGIN_ALERT", "OTP_CODE"])
            f = {"pod": pod, "msg_id": mid, "customer": cust, "template": tpl, "to": to, "provider": "relaytel"}
            if tpl == "OTP_CODE":
                f["otp"] = "[REDACTED]"
            lines.append(_log(ms, "INFO", svc, "sms.send", {**f, "status": "ACCEPTED"}))
            lines.append(_log(ms + rng.randint(1500, 6000), "INFO", svc, "sms.dlr",
                              {"pod": pod, "msg_id": mid, "to": to, "status": "DELIVERED",
                               "carrier_latency_ms": rng.randint(900, 5200)}))
        elif kind == "email":
            lines.append(_log(ms, "INFO", svc, "email.send",
                              {"pod": pod, "msg_id": msg_id("EML"), "customer": cust,
                               "template": rng.choice(["STATEMENT_READY", "PAYMENT_RECEIVED", "PROFILE_CHANGED"]),
                               "to": f"{rng.choice('bcdefghkmnprt')}****{rng.choice('aeiklnrsty')}@example.com",
                               "provider": "mailrelay", "status": "SENT"}))
        elif kind == "push":
            lines.append(_log(ms, "INFO", svc, "push.send",
                              {"pod": pod, "msg_id": msg_id("PSH"), "customer": cust,
                               "template": rng.choice(["PAYMENT_RECEIVED", "LOW_BALANCE", "CARD_USED"]),
                               "device": f"dev-{_other4(rng)}", "platform": rng.choice(["ios", "android"]),
                               "status": "DELIVERED"}))
        elif kind == "health":
            lines.append(_log(ms, "DEBUG", svc, "health.check", {"pod": pod, "status": "ok",
                                                                 "providers": "relaytel,mailrelay,pushgw"}))
        else:
            lines.append(_log(ms, "INFO", svc, "queue.metrics", {"pod": pod, "queue": rng.choice(["notify-high", "notify-bulk"]),
                                                                 "depth": rng.randint(0, 120),
                                                                 "oldest_ms": rng.randint(5, 1800)}))
    other_to, other_cust = _OTHER_ALERT["phone"], _OTHER_ALERT["customer"]
    other_sms = msg_id("SMS")
    sms_james, push_james = "SMS-5b7e21", "PSH-a93c07"
    lines += [
        _log(_at("21:25:18.330", 25), "WARN", svc, "sms.provider_throttle",
             {"pod": "notify-2", "provider": "relaytel", "retry_after_ms": 500, "affected": 12}),
        _log(_at("03:05:47.719"), "WARN", svc, "email.bounce",
             {"pod": "notify-1", "msg_id": msg_id("EML"), "reason": "mailbox_full", "action": "retry_in_6h"}),
        # the other customer's ATM alert (FRD-77264) — delivered normally
        _log(_at("22:17:41.002", 25), "INFO", svc, "notify.request",
             {"pod": "notify-1", "alert_id": _OTHER_ALERT["alert_id"], "customer": other_cust, "template": "FRAUD_ALERT_ATM",
              "channels": "sms,push"}),
        _log(_at("22:17:41.240", 25), "INFO", svc, "sms.send",
             {"pod": "notify-1", "msg_id": other_sms, "customer": other_cust, "template": "FRAUD_ALERT_ATM",
              "to": other_to, "provider": "relaytel", "status": "ACCEPTED"}),
        _log(_at("22:17:44.918", 25), "INFO", svc, "sms.dlr",
             {"pod": "notify-1", "msg_id": other_sms, "to": other_to, "status": "DELIVERED", "carrier_latency_ms": 3678}),
        # --- planted: James (CUST-1002) ---
        _log(_at("23:44:12.871", 25), "INFO", svc, "contact.update",
             {"pod": "notify-2", "customer": "CUST-1002", "field": "phone", "old": "+1******4402", "old_verified": "true",
              "new": "+1******0199", "new_verified": "false", "source": "mobile_app", "device": "new-android-device",
              "ip_country": "RO"}),
        _log(_at("23:44:13.409", 25), "INFO", svc, "email.send",
             {"pod": "notify-2", "msg_id": "EML-4f19d2", "customer": "CUST-1002", "template": "PROFILE_CHANGED",
              "to": "j****n@example.com", "provider": "mailrelay", "status": "SENT"}),
        _log(_at("02:35:41.512"), "INFO", svc, "notify.request",
             {"pod": "notify-1", "alert_id": "FRD-77310", "customer": "CUST-1002", "template": "FRAUD_ALERT_CNP",
              "channels": "sms,push"}),
        _log(_at("02:35:41.530"), "WARN", svc, "contact.recent_change",
             {"pod": "notify-1", "customer": "CUST-1002", "field": "phone", "changed_minutes_ago": 171,
              "policy_check": "skipped", "note": "recent-change rule not enforced by notification-service"}),
        _log(_at("02:36:02.114"), "INFO", svc, "sms.send",
             {"pod": "notify-1", "msg_id": sms_james, "customer": "CUST-1002", "alert_id": "FRD-77310",
              "template": "FRAUD_ALERT_CNP", "to": "+1******0199", "provider": "relaytel", "status": "ACCEPTED"}),
        _log(_at("02:36:02.388"), "ERROR", svc, "push.send",
             {"pod": "notify-1", "msg_id": push_james, "customer": "CUST-1002", "alert_id": "FRD-77310",
              "template": "FRAUD_ALERT_CNP", "device": "dev-3c91", "platform": "android", "status": "FAILED",
              "error": "provider returned UNREGISTERED: device token expired (last refreshed 2026-06-19)",
              "retry": "false"}),
        _log(_at("02:36:07.905"), "INFO", svc, "sms.dlr",
             {"pod": "notify-1", "msg_id": sms_james, "to": "+1******0199", "status": "DELIVERED",
              "carrier_latency_ms": 5791}),
    ]
    return lines


def service_logs(card_last4: str = "7731") -> dict[str, str]:
    """Raw logs of card-auth, fraud-engine and notification-service around the fraud burst on a card.

    Structured lines (ISO timestamp, level, service, event, key=value fields), 2026-09-25T20:00Z to
    2026-09-26T04:00Z, mostly routine noise from other (masked) customers. The planted facts are in LOG_FACTS.

    Args:
        card_last4: Card whose incident to show. Only "7731" (James Chen) has an incident in this dataset.
    """
    if card_last4 != LOG_FACTS["card_last4"]:
        raise ValueError(f"no incident logs for card ending {card_last4}; only {LOG_FACTS['card_last4']} has one")
    out = {}
    for i, (name, build) in enumerate((("card-auth", _card_auth_log), ("fraud-engine", _fraud_engine_log),
                                       ("notification-service", _notification_log))):
        out[name] = "\n".join(sorted(build(_random.Random(7731_00 + i)))) + "\n"
    return out


# --------------------------------------------------------------------------------------
# Raw core-banking customer record (40 top-level fields; only SUPPORT_FIELDS matter for support)
# --------------------------------------------------------------------------------------
SUPPORT_FIELDS: list[str] = ["name", "segment", "preferred_channel", "card_status", "account_ids", "kyc_status"]

_CORE_EXTRAS: dict[str, dict[str, Any]] = {
    "CUST-1001": {
        "cif_no": "0017041201", "name_legal": "MARTINEZ, SOFIA", "segment_cd": "PRM", "branch_cd": "ATX-014",
        "relationship_mgr_id": "RM-2231",
        "address": {"line1": "4810 Live Oak Bend", "line2": None, "city": "Austin", "state": "TX",
                    "postal_code": "78745", "country_cd": "US", "addr_type_cd": "RES",
                    "verified_ts": "2024-03-02T15:11:09Z"},
        "phone_masked": "+1******0142", "phone_last_changed_ts": "2023-02-11T16:20:05Z",
        "email_masked": "s****z@example.com", "dob_masked": "1989-**-**",
        "kyc_last_review_ts": "2025-11-04T10:02:51Z", "kyc_risk_rating": "LOW",
        "card_product_cd": "VPR-2", "overdraft_protection_cd": "LINKED_SAV", "stmt_cycle_day": 14,
        "paperless_flg": "Y", "marketing_opt_in_flg": "N", "mfa_method_cd": "APP_PUSH",
        "last_login_ts": "2026-09-24T19:12:40Z", "record_version": 57, "created_ts": "2017-04-12T14:33:20Z",
        "updated_ts": "2026-09-22T09:41:03Z", "updated_by": "svc-los-sync",
    },
    "CUST-1002": {
        "cif_no": "0021110302", "name_legal": "CHEN, JAMES", "segment_cd": "EVD", "branch_cd": "SEA-007",
        "relationship_mgr_id": None,
        "address": {"line1": "901 Pinecrest Ave", "line2": "Apt 4B", "city": "Seattle", "state": "WA",
                    "postal_code": "98103", "country_cd": "US", "addr_type_cd": "RES",
                    "verified_ts": "2021-11-03T17:09:12Z"},
        "phone_masked": "+1******0199", "phone_last_changed_ts": "2026-09-25T23:44:12Z",
        "email_masked": "j****n@example.com", "dob_masked": "1986-**-**",
        "kyc_last_review_ts": "2025-10-18T13:27:40Z", "kyc_risk_rating": "LOW",
        "card_product_cd": "VEV-1", "overdraft_protection_cd": "STD", "stmt_cycle_day": 3,
        "paperless_flg": "Y", "marketing_opt_in_flg": "Y", "mfa_method_cd": "SMS_OTP",
        "last_login_ts": "2026-09-25T23:41:07Z", "record_version": 23, "created_ts": "2021-11-03T17:05:44Z",
        "updated_ts": "2026-09-25T23:44:12Z", "updated_by": "svc-mobile-profile",
    },
    "CUST-1003": {
        "cif_no": "0025062003", "name_legal": "PATEL, AISHA", "segment_cd": "EVD", "branch_cd": "CHI-022",
        "relationship_mgr_id": None,
        "address": {"line1": "3345 N Lakeview Ct", "line2": "Unit 2", "city": "Chicago", "state": "IL",
                    "postal_code": "60657", "country_cd": "US", "addr_type_cd": "RES",
                    "verified_ts": "2025-06-20T15:10:31Z"},
        "phone_masked": "+1******0176", "phone_last_changed_ts": "2025-06-20T15:02:44Z",
        "email_masked": "a****l@example.com", "dob_masked": "1998-**-**",
        "kyc_last_review_ts": "2025-06-20T15:02:44Z", "kyc_risk_rating": "LOW",
        "card_product_cd": "VEV-1", "overdraft_protection_cd": None, "stmt_cycle_day": 20,
        "paperless_flg": "N", "marketing_opt_in_flg": "N", "mfa_method_cd": "APP_PUSH",
        "last_login_ts": "2026-09-23T08:05:13Z", "record_version": 9, "created_ts": "2025-06-20T15:02:44Z",
        "updated_ts": "2026-09-24T11:26:30Z", "updated_by": "svc-los-sync",
    },
}


def core_banking_record(customer_id: str) -> dict[str, Any]:
    """The raw core-banking customer record (40 fields: internal codes, flags, audit data, nested address).

    Only SUPPORT_FIELDS matter for a typical support question; the rest is what pruning removes.

    Args:
        customer_id: Bank customer id, e.g. "CUST-1002".
    """
    c, x = CUSTOMERS.get(customer_id), _CORE_EXTRAS.get(customer_id)
    if not c or not x:
        return {"error": f"unknown customer {customer_id}"}
    return {
        "cif_no": x["cif_no"], "customer_id": customer_id, "party_type_cd": "IND", "name": c["name"],
        "name_legal": x["name_legal"], "segment": c["segment"], "segment_cd": x["segment_cd"],
        "preferred_channel": c["preferred_channel"], "language_cd": f"{c['language']}-US",
        "customer_since": c["customer_since"], "branch_cd": x["branch_cd"],
        "relationship_mgr_id": x["relationship_mgr_id"], "address": dict(x["address"]),
        "phone_masked": x["phone_masked"], "phone_last_changed_ts": x["phone_last_changed_ts"],
        "email_masked": x["email_masked"], "dob_masked": x["dob_masked"], "kyc_status": "verified",
        "kyc_last_review_ts": x["kyc_last_review_ts"], "kyc_risk_rating": x["kyc_risk_rating"],
        "pep_screen_result": "NO_MATCH", "sanctions_screen_ts": "2026-09-25T04:00:12Z",
        "card_last4": c["card_last4"], "card_status": CARD_STATUS[c["card_last4"]],
        "card_product_cd": x["card_product_cd"], "account_ids": [a["account_id"] for a in ACCOUNTS[customer_id]],
        "overdraft_protection_cd": x["overdraft_protection_cd"], "stmt_cycle_day": x["stmt_cycle_day"],
        "paperless_flg": x["paperless_flg"], "marketing_opt_in_flg": x["marketing_opt_in_flg"],
        "credit_bureau_ref": c["bureau_ref"], "mfa_method_cd": x["mfa_method_cd"],
        "last_login_ts": x["last_login_ts"], "deceased_dt": None, "bankruptcy_case_no": None,
        "notes_internal": None, "record_version": x["record_version"], "created_ts": x["created_ts"],
        "updated_ts": x["updated_ts"], "updated_by": x["updated_by"],
    }


# --------------------------------------------------------------------------------------
# A large transaction CSV for the sandbox (Code Interpreter) demo, with plain-Python ground truth
# --------------------------------------------------------------------------------------
_LARGE_START = _dt.date(2026, 6, 1)
_LARGE_DAYS = 118                                    # 2026-06-01 .. 2026-09-26
_LARGE_CARDS = ("4417", "7731", "2208")
_LARGE_CARD_WEIGHTS = (0.40, 0.35, 0.25)
_LARGE_HEADER = ("txn_id,card_last4,date,time,merchant,mcc,category,amount,currency,country,channel,status,"
                 "decline_code")
_LARGE_DECLINE_CODES = ("51", "05", "N7", "54", "61", "65", "91")
_LARGE_POOLS: dict[str, dict[str, float]] = {       # a subset of each card's merchants plus travel merchants
    "4417": {**_CARD_PROFILES["4417"]["merchants"], "Mercado Oaxaca": 1, "Playa Azul Resort": 0.5},
    "7731": {**_CARD_PROFILES["7731"]["merchants"], "Maple Leaf Books": 1},
    "2208": dict(_CARD_PROFILES["2208"]["merchants"]),
}
_LARGE_BASE_DECLINE = {"4417": 0.05, "7731": 0.07, "2208": 0.06}


def large_transactions_csv(n: int = 50_000, seed: int = 42) -> str:
    """A CSV (header + n rows) of card transactions for cards 4417, 7731 and 2208, 2026-06-01..2026-09-26.

    Rows are in time order and include declines. Deterministic for a given (n, seed).

    Args:
        n: Number of data rows.
        seed: Random seed.
    """
    rng = _random.Random(seed)
    rand, randrange, uniform = rng.random, rng.randrange, rng.uniform
    keys = sorted(randrange(_LARGE_DAYS) * 1440 + _bisect.bisect(_HOUR_CUM, rand() * _HOUR_CUM[-1]) * 60
                  + randrange(60) for _ in range(n))
    cards = rng.choices(_LARGE_CARDS, weights=_LARGE_CARD_WEIGHTS, k=n)
    dates = [(_LARGE_START + _dt.timedelta(days=d)).isoformat() for d in range(_LARGE_DAYS)]
    times = [f"{m // 60:02d}:{m % 60:02d}" for m in range(1440)]
    aug = range((_dt.date(2026, 8, 1) - _LARGE_START).days, (_dt.date(2026, 9, 1) - _LARGE_START).days)
    pools = {}
    for card, weights in _LARGE_POOLS.items():
        names = list(weights)
        pools[card] = (names, list(itertools.accumulate(weights[k] for k in names)))
    lines = [_LARGE_HEADER]
    for i in range(n):
        day, minute = divmod(keys[i], 1440)
        card = cards[i]
        names, cum = pools[card]
        merchant = names[_bisect.bisect(cum, rand() * cum[-1])]
        mcc, category, country, channel, lo, hi = _MERCHANT_CATALOG[merchant]
        amount = lo if lo == hi else uniform(lo, hi)
        p = _LARGE_BASE_DECLINE[card]
        if card == "7731" and merchant == "GameVault Online":   # planted: James's game subscription bounces in August
            p = 0.55 if day in aug else 0.12
        if rand() < p:
            status, code = "declined", ("51" if p > 0.1 else _LARGE_DECLINE_CODES[randrange(7)])
        else:
            status, code = "approved", ""
        lines.append(f"LT-{i + 1:06d},{card},{dates[day]},{times[minute]},{merchant},{mcc},{category},"
                     f"{amount:.2f},USD,{country},{channel},{status},{code}")
    return "\n".join(lines) + "\n"


def large_txn_truth(n: int = 50_000, seed: int = 42) -> dict[str, Any]:
    """Ground truth for large_transactions_csv(n, seed), computed with plain Python from the CSV text.

    Args:
        n: Number of data rows (same as for the CSV).
        seed: Random seed (same as for the CSV).
    """
    per = {c: {"count": 0, "declined": 0, "aug_approved": 0, "aug_all": 0} for c in _LARGE_CARDS}
    declined_7731_aug: dict[str, int] = {}
    reader = _csv.reader(_io.StringIO(large_transactions_csv(n, seed)))
    next(reader)
    for _id, card, date, _time, merchant, _mcc, _cat, amount, _cur, _ctry, _ch, status, _code in reader:
        p = per[card]
        p["count"] += 1
        cents = int(amount.replace(".", ""))
        if status == "declined":
            p["declined"] += 1
        if date.startswith("2026-08"):
            p["aug_all"] += cents
            if status == "approved":
                p["aug_approved"] += cents
            elif card == "7731":
                declined_7731_aug[merchant] = declined_7731_aug.get(merchant, 0) + 1
    ranked = sorted(declined_7731_aug.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "n": n, "seed": seed, "date_range": [_LARGE_START.isoformat(), "2026-09-26"],
        "per_card": {c: {"count": p["count"], "declined": p["declined"],
                         "aug_2026_approved_usd": round(p["aug_approved"] / 100, 2),
                         "aug_2026_all_usd": round(p["aug_all"] / 100, 2)} for c, p in per.items()},
        "top_declined_merchant_7731_aug_2026": ranked[0][0] if ranked else None,
        "top_declined_count_7731_aug_2026": ranked[0][1] if ranked else 0,
        "runner_up_declined_count_7731_aug_2026": ranked[1][1] if len(ranked) > 1 else 0,
        "definitions": {
            "count": "rows for the card", "declined": "rows with status == 'declined'",
            "aug_2026_approved_usd": "sum of amount, status == 'approved', date in 2026-08",
            "aug_2026_all_usd": "sum of amount, any status, date in 2026-08",
            "top_declined_merchant_7731_aug_2026": "merchant with the most declined rows for card 7731 in 2026-08",
        },
    }


# --------------------------------------------------------------------------------------
# Last week's support sessions (input for AgentCore Memory in M02 §6)
# --------------------------------------------------------------------------------------
SOFIA_FACTS: dict[str, Any] = {
    "customer_id": "CUST-1001",
    "session_date": "2026-09-19",
    "preferred_name": "Sofia",
    "contact_preference": "email",           # email only
    "never_channel": "SMS",
    "remodel_start": "November 2026",
    "contractor": "Oakline Remodeling",
    "contractor_quote_usd": 24_600,
    "loan_application_id": "APP-2001",
    "loan_amount_usd": 25_000,
    "loan_term_months": 60,
    "dispute_txn_id": "T-88342",
    "dispute_merchant": "Harbor Electronics",
    "dispute_case_id": "CASE-4907",
    "dispute_case_type": "billing_dispute",
    "dispute_policy": "DP-2026.2",
}

SOFIA_LAST_SESSION: list[dict[str, str]] = [
    {"role": "user", "text": "Hi there. Before anything else: please call me Sofia. The app keeps greeting me as "
                             "\"Ms. Martinez\" and it makes me feel like I'm at the dentist."},
    {"role": "assistant", "text": "Of course, Sofia, happy to. You're signed in, so I can see your accounts and your "
                                  "card ending 4417. What can I help you with today?"},
    {"role": "user", "text": "Two things. The fun one first: we're finally redoing the kitchen. The cabinet door "
                             "under the sink fell off in July and that was the last straw. The other thing is a charge "
                             "on my card that looks wrong, but let's start with the kitchen."},
    {"role": "assistant", "text": "Congratulations on the project. Are you looking at financing the remodel with us?"},
    {"role": "user", "text": "Yes. We got three quotes. The one we're going with is Oakline Remodeling, $24,600 all "
                             "in: cabinets, quartz counters, the sink, plumbing and labor. The other two were higher, "
                             "and one of them couldn't even look at it until next spring."},
    {"role": "assistant", "text": "Thanks, that's useful. For a project like this our home improvement loan is "
                                  "usually the closest fit. Under lending policy LP-2026.3 it has APRs from 7.99% to "
                                  "12.49%, amounts from $5,000 to $100,000 and terms from 24 to 120 months. It's "
                                  "unsecured, so it doesn't put a lien on your home. The rate you'd get is set in "
                                  "underwriting."},
    {"role": "user", "text": "Okay. I'd like to ask for $25,000 so there's a little cushion over the quote for "
                             "surprises behind the walls. Timing matters too: Oakline has us on their calendar to "
                             "start in November, so I'd like the money sorted out well before that."},
    {"role": "assistant", "text": "Understood: $25,000 for a remodel that starts this November. The application "
                                  "needs three documents: a recent pay stub, a bank statement and a government ID. I "
                                  "can start the application now, and you can upload the documents whenever you're "
                                  "ready."},
    {"role": "user", "text": "Please do. What term should I pick? My first thought was 36 months, to get it over "
                             "with quickly."},
    {"role": "assistant", "text": "I can't recommend a term, but I can show you the numbers from the payment "
                                  "calculator. At the lowest advertised rate of 7.99%, $25,000 over 36 months is "
                                  "$783.29 a month, and over 60 months it's $506.79 a month. The actual rate is set "
                                  "in underwriting, so treat these as examples."},
    {"role": "user", "text": "Oof. $783 a month is more than I want to commit to, especially with daycare costs "
                             "going up next year. Let's do 60 months. Please put 60 months on the application."},
    {"role": "assistant", "text": "Done. I've started application APP-2001: home improvement loan, $25,000, 60-month "
                                  "term. It stays in draft until the pay stub, bank statement and ID are uploaded, "
                                  "and then it moves on to review."},
    {"role": "user", "text": "Great. I'll scan everything tomorrow, Sunday is my paperwork day. Is it true you'll be "
                             "able to see the documents once they're uploaded?"},
    {"role": "assistant", "text": "Yes. Once they're uploaded they're attached to the application and I can see its "
                                  "status. I can't tell you how long the review will take, but the status shows each "
                                  "step as it happens."},
    {"role": "user", "text": "Fine by me. Okay, now the annoying thing. Yesterday I ordered a soundbar from Harbor "
                             "Electronics online and my card was charged twice, $489.99 two times. I promise I only "
                             "bought one soundbar. We are not a two-soundbar family."},
    {"role": "assistant", "text": "Let me check your card ending 4417. I see two online charges of $489.99 at Harbor "
                                  "Electronics on 2026-09-18: T-88341 and T-88342. Same merchant, same amount, same "
                                  "day, so the duplicate check flags them as a pair."},
    {"role": "user", "text": "Yes, T-88342, the second one, is the duplicate. The first one is the soundbar."},
    {"role": "assistant", "text": "Thank you. I've opened a billing dispute for T-88342: case CASE-4907. Under "
                                  "dispute policy DP-2026.2, a duplicate charge gets a provisional credit within 10 "
                                  "business days while we contact the merchant, and you're well inside the 60-day "
                                  "filing window."},
    {"role": "user", "text": "Perfect. Will you send me a text when the credit shows up?"},
    {"role": "assistant", "text": "I can't promise a message that the system hasn't scheduled. You can check CASE-4907 "
                                  "at any time in the app or by asking us. Speaking of messages: how would you like "
                                  "us to contact you when we do need to reach you?"},
    {"role": "user", "text": "Email. Email only, please, never SMS. I get so many scam texts pretending to be banks "
                             "that I delete every text without reading it. If it arrives as a text, it doesn't exist "
                             "to me."},
    {"role": "assistant", "text": "Understood: email only, never SMS. I've recorded that as your contact preference, "
                                  "and we won't use texts for anything."},
    {"role": "user", "text": "Thank you! Random question while I have you: quartz or granite? Oakline says quartz, "
                             "my brother-in-law swears by granite."},
    {"role": "assistant", "text": "I'm afraid countertops are outside what I can help with; I can only help with your "
                                  "banking. Oakline are probably the best people to ask."},
    {"role": "user", "text": "Ha, fair enough. Does any of this touch my savings account? I'd rather not dip into it "
                             "for the kitchen."},
    {"role": "assistant", "text": "No. Your savings account SAV-1001-02 isn't affected by the loan application or by "
                                  "the dispute. Whether to use savings or borrow is your decision; I can explain the "
                                  "products, but I can't advise you on that."},
    {"role": "user", "text": "My pay stub is from Brightwave Solar and we get paid semi-monthly. Is one stub enough, "
                             "or do you need two?"},
    {"role": "assistant", "text": "One recent pay stub is what the application asks for. The underwriting team reviews "
                                  "all three documents together and will ask if they need anything else."},
    {"role": "user", "text": "Great. Thanks, that was painless. Honestly easier than the last time I tried to do "
                             "something like this at a branch."},
    {"role": "assistant", "text": "Glad to hear it, Sofia. Enjoy the rest of your weekend, and good luck with the "
                                  "kitchen."},
]

AISHA_FACTS: dict[str, Any] = {
    "customer_id": "CUST-1003",
    "session_date": "2026-09-17",
    "contact_preference": "app",              # in-app notifications
    "moving_to": "Denver",
    "move_month": "December 2026",
}

AISHA_LAST_SESSION: list[dict[str, str]] = [
    {"role": "user", "text": "Hi, Aisha Patel here. Heads-up that I'm moving: my company is transferring me to the "
                             "Denver office, and I'll be relocating in December."},
    {"role": "assistant", "text": "Thanks for letting us know, Aisha, and congratulations on the transfer. When you "
                                  "have the new address you can update it in the app or with us; an address change "
                                  "needs a quick extra verification step. Is there anything you'd like to set up "
                                  "now?"},
    {"role": "user", "text": "Not yet, the lease isn't signed. One thing though: please use app notifications for "
                             "anything about my account. The app is the only thing I actually look at."},
    {"role": "assistant", "text": "Noted: in-app notifications as your preferred channel. I've recorded that "
                                  "preference."},
    {"role": "user", "text": "Will my card still work in Colorado, or do I need a new one when I move?"},
    {"role": "assistant", "text": "Your card works across the US, so you won't need a new card or a travel notice for "
                                  "a move within the country. Once your address is updated, statements and any "
                                  "replacement card will go to the new address."},
    {"role": "user", "text": "Perfect, that's everything for now. I'll message again once I have the new address."},
    {"role": "assistant", "text": "Sounds good, Aisha. Good luck with the move."},
]


# ======================================================================================
# M02 — AgentCore Gateway tool catalog + long-context haystack (appended; nothing above changes)
#   bank_tool_catalog(): 335 tool definitions in Gateway ToolDefinition shape, split into 3 Lambda
#   targets (175 / 150 / 10, the slide-46 shape). LABELLED_TOOL_REQUESTS: 10 requests -> the one
#   correct gateway tool. ops_log_haystack(): a realistic operations log of a chosen token size with
#   "needles" planted at relative depths. Deterministic; verified in a test account with Nova 2 Lite, 2026-09-26.
# ======================================================================================

# Gateway target names: [0-9a-zA-Z] only. The API forbids "_" in target names, and Nova v1 models fail to CALL a
# tool whose name contains "-" ("Model produced invalid sequence as part of ToolUse"). The gateway exposes each tool
# as "<target>___<tool>" (three underscores), e.g. "CardsPayments___card_block".
from collections.abc import Iterable as _Iterable  # noqa: E402

TOOL_TARGET_SIZES: dict[str, int] = {"CoreBanking": 175, "CardsPayments": 150, "CustomerSupport": 10}

# verb -> (description template, extra params)
_GW_VERBS: dict[str, tuple[str, list[str]]] = {
    "get":      ("Retrieve one {noun} by its id and return its current details.", []),
    "list":     ("List the {plural} that belong to a customer, newest first (max 20).", []),
    "search":   ("Search {plural} across customers by free-text query (name, reference, amount).", ["query"]),
    "create":   ("Create a new {noun} for a customer.", ["details"]),
    "update":   ("Update fields on an existing {noun}.", ["changes"]),
    "close":    ("Close or cancel an existing {noun}; it can no longer be used afterwards.", ["reason"]),
    "history":  ("Return the audit history (who changed what, when) of one {noun}.", []),
    "export":   ("Export {plural} as a PDF or CSV document for download.", ["format"]),
    "quote":    ("Price a new {noun} without creating it: rate, fees and monthly payment.", ["amount", "term_months"]),
    "status":   ("Return the processing status of a {noun} (pending, approved, rejected).", []),
    "approve":  ("Approve a pending {noun} (staff only).", ["approver_id"]),
    "reject":   ("Reject a pending {noun} with a reason (staff only).", ["reason"]),
    "validate": ("Validate the data of a {noun} before submission and return any errors.", ["details"]),
    "summary":  ("Return aggregated totals for a customer's {plural} (count, sum, average).", []),
    "notify":   ("Send the customer a notification about their {noun}.", ["channel"]),
}

_GW_PARAMS: dict[str, dict[str, str]] = {
    "customer_id":  {"type": "string", "description": "AnyCompany customer id, e.g. CUST-1001"},
    "query":        {"type": "string", "description": "Free-text search terms"},
    "details":      {"type": "object", "description": "Fields for the new record"},
    "changes":      {"type": "object", "description": "Fields to change and their new values"},
    "reason":       {"type": "string", "description": "Short reason, stored for audit"},
    "format":       {"type": "string", "description": "pdf or csv"},
    "amount":       {"type": "number", "description": "Amount in USD"},
    "term_months":  {"type": "integer", "description": "Term in months"},
    "approver_id":  {"type": "string", "description": "Staff id of the approver"},
    "channel":      {"type": "string", "description": "email, sms or app"},
    "card_last4": {"type": "string", "description": "Last 4 digits of the card"},
    "transaction_id": {"type": "string", "description": "Card transaction id"},
    "from_currency": {"type": "string", "description": "ISO currency code, e.g. EUR"},
    "to_currency": {"type": "string", "description": "ISO currency code, e.g. USD"},
    "subject": {"type": "string", "description": "One-line summary"},
    "description": {"type": "string", "description": "Full description of the issue"},
    "priority": {"type": "string", "description": "low, normal or high"},
    "ticket_id": {"type": "string", "description": "Support ticket id"},
    "team": {"type": "string", "description": "Which specialist team should call"},
    "preferred_time": {"type": "string", "description": "ISO date-time for the callback"},
    "score": {"type": "integer", "description": "1-5"},
    "comment": {"type": "string", "description": "Free text"},
}
_GW_OPTIONAL = ("details", "changes", "format", "channel", "priority", "comment")

# (entity, noun, plural, id_param, hint, verbs): each verb becomes one generated tool "<entity>_<verb>"
_GW_CORE: list[tuple] = [
    ("customer_profile", "customer profile", "customer profiles", "customer_id", "Profile = name, segment, contact preference, customer-since date.", ["get", "search", "update", "history", "export", "summary", "validate"]),
    ("customer_address", "customer address", "customer addresses", "customer_id", "Postal and mailing addresses on file; use update to change where letters and cards are sent.", ["get", "list", "create", "update", "close", "history", "validate"]),
    ("customer_contact", "contact detail", "contact details", "customer_id", "Phone numbers and email addresses used for alerts and one-time passcodes.", ["get", "list", "create", "update", "close", "history", "validate"]),
    ("marketing_consent", "marketing consent", "marketing consents", "customer_id", "Opt-in/opt-out flags for marketing by channel (GDPR/CAN-SPAM).", ["get", "list", "update", "history", "export", "notify", "summary"]),
    ("kyc_check", "KYC check", "KYC checks", "check_id", "Know-your-customer identity verification (ID document + selfie).", ["get", "list", "create", "status", "approve", "reject", "history"]),
    ("aml_screening", "AML screening", "AML screenings", "screening_id", "Anti-money-laundering sanctions and PEP screening of a customer.", ["get", "list", "create", "status", "approve", "reject", "history"]),
    ("checking_account", "checking account", "checking accounts", "account_id", "Everyday current account with debit card and overdraft.", ["get", "list", "create", "update", "close", "history", "summary"]),
    ("savings_account", "savings account", "savings accounts", "account_id", "Instant-access savings account that earns variable interest.", ["get", "list", "create", "update", "close", "history", "summary"]),
    ("term_deposit", "term deposit", "term deposits", "deposit_id", "Fixed-term deposit (3-60 months) at a locked interest rate; early withdrawal has a penalty.", ["get", "list", "create", "close", "quote", "history", "notify"]),
    ("account_balance", "account balance", "account balances", "account_id", "Available and ledger balance of an account, real time.", ["get", "list", "summary", "history", "export", "notify", "search"]),
    ("account_statement", "account statement", "account statements", "statement_id", "Monthly account statement; export returns a downloadable PDF for a given month.", ["get", "list", "export", "search", "notify", "history", "summary"]),
    ("account_transaction", "account transaction", "account transactions", "transaction_id", "Posted and pending transactions on a checking or savings account.", ["get", "list", "search", "export", "summary", "history", "notify"]),
    ("standing_order", "standing order", "standing orders", "order_id", "Recurring fixed payment the customer sends on a schedule (e.g. monthly rent).", ["get", "list", "create", "update", "close", "history", "validate"]),
    ("direct_debit", "direct debit", "direct debits", "mandate_id", "Mandate that lets a merchant pull variable payments from the account.", ["get", "list", "create", "update", "close", "history", "search"]),
    ("beneficiary", "saved beneficiary", "saved beneficiaries", "beneficiary_id", "Saved payee details (name, routing and account number) for faster transfers.", ["get", "list", "create", "update", "close", "validate", "search"]),
    ("overdraft", "overdraft arrangement", "overdraft arrangements", "account_id", "Arranged overdraft limit on a checking account and its interest.", ["get", "create", "update", "close", "quote", "status", "notify"]),
    ("personal_loan", "personal loan", "personal loans", "loan_id", "Unsecured instalment loan, $1,000-$50,000, 12-84 months.", ["get", "list", "quote", "create", "status", "approve", "reject"]),
    ("mortgage", "mortgage", "mortgages", "mortgage_id", "Home loan secured on property; includes rate type and remaining term.", ["get", "list", "quote", "create", "status", "update", "history"]),
    ("loan_repayment", "loan repayment", "loan repayments", "loan_id", "Scheduled and extra repayments on a loan or mortgage, including payoff amount.", ["get", "list", "create", "quote", "history", "export", "summary"]),
    ("credit_score", "internal credit score", "internal credit scores", "customer_id", "The bank's internal risk score and the latest bureau score.", ["get", "history", "summary", "export", "validate", "status", "notify"]),
    ("collateral", "collateral item", "collateral items", "collateral_id", "Assets pledged as security for a secured loan (property, vehicle, deposit).", ["get", "list", "create", "update", "close", "validate", "history"]),
    ("branch_appointment", "branch appointment", "branch appointments", "appointment_id", "In-person meeting at a branch with a banker.", ["get", "list", "create", "update", "close", "notify", "search"]),
    ("tax_document", "tax document", "tax documents", "document_id", "Year-end tax forms (1099-INT, 1098 mortgage interest).", ["get", "list", "export", "notify", "search", "history", "summary"]),
    ("investment_portfolio", "investment portfolio", "investment portfolios", "portfolio_id", "Managed investment portfolio: holdings, allocation and performance.", ["get", "list", "summary", "history", "export", "update", "notify"]),
    ("power_of_attorney", "power of attorney record", "power of attorney records", "poa_id", "Third party legally authorised to act on the customer's accounts.", ["get", "list", "create", "update", "close", "validate", "status"]),
]

_GW_CARDS: list[tuple] = [
    ("card", "payment card", "payment cards", "card_last4", "Debit or credit card; get returns status, expiry and product.", ["get", "list", "search", "history", "summary", "export"]),
    ("card_transaction", "card transaction", "card transactions", "transaction_id", "Authorisations and settled purchases made with a card.", ["get", "list", "search", "export", "summary", "history"]),
    ("dispute", "card dispute", "card disputes", "dispute_id", "Dispute of a card charge the customer does not recognise or did not authorise; opens a chargeback case.", ["get", "list", "update", "close", "status", "history"]),
    ("chargeback", "chargeback", "chargebacks", "chargeback_id", "Network chargeback raised with the merchant's acquirer after a dispute.", ["get", "list", "create", "status", "approve", "reject"]),
    ("fraud_alert", "fraud alert", "fraud alerts", "alert_id", "Suspicious-activity alert raised by the fraud engine on a card.", ["get", "list", "update", "close", "history", "notify"]),
    ("travel_notice", "travel notice", "travel notices", "notice_id", "Tells the fraud engine the customer is travelling abroad so card use is not declined.", ["get", "list", "create", "update", "close", "validate"]),
    ("domestic_payment", "domestic payment", "domestic payments", "payment_id", "One-off ACH payment to a US bank account.", ["get", "list", "create", "close", "status", "validate"]),
    ("wire_transfer", "international wire transfer", "international wire transfers", "wire_id", "SWIFT wire to a foreign bank account, with FX conversion.", ["get", "list", "create", "close", "status", "quote"]),
    ("internal_transfer", "internal transfer", "internal transfers", "transfer_id", "Move money between two accounts of the same customer.", ["get", "list", "create", "close", "status", "validate"]),
    ("p2p_payment", "person-to-person payment", "person-to-person payments", "p2p_id", "Send money to a friend by phone number or email.", ["get", "list", "create", "close", "status", "notify"]),
    ("bill_payment", "bill payment", "bill payments", "bill_id", "Pay a utility or credit card bill to a registered biller.", ["get", "list", "create", "update", "close", "status"]),
    ("payee", "biller", "billers", "payee_id", "Registered biller (utility, telco, insurer) for bill pay.", ["get", "list", "create", "update", "close", "search"]),
    ("recurring_card_payment", "recurring card payment", "recurring card payments", "subscription_id", "Merchant subscriptions charged to a card (streaming, gym).", ["get", "list", "close", "search", "history", "summary"]),
    ("refund", "merchant refund", "merchant refunds", "refund_id", "Credit from a merchant back to the card.", ["get", "list", "search", "status", "history", "summary"]),
    ("rewards", "rewards balance", "rewards balances", "card_last4", "Card cash-back and points balance and redemptions.", ["get", "list", "history", "summary", "export", "notify"]),
    ("credit_limit", "credit limit", "credit limits", "card_last4", "Credit card credit line; increase requests go through underwriting.", ["get", "update", "quote", "status", "history", "notify"]),
    ("virtual_card", "virtual card number", "virtual card numbers", "virtual_card_id", "Single-use or merchant-locked virtual card number for online shopping.", ["get", "list", "create", "close", "update", "history"]),
    ("wallet_token", "mobile wallet token", "mobile wallet tokens", "token_id", "Card token provisioned to Apple Pay / Google Pay.", ["get", "list", "create", "close", "status", "history"]),
    ("card_statement", "credit card statement", "credit card statements", "statement_id", "Monthly credit-card statement with minimum payment due.", ["get", "list", "export", "notify", "summary", "search"]),
    ("merchant", "merchant record", "merchant records", "merchant_id", "Merchant name, category code and location behind a card charge.", ["get", "search", "list", "history", "summary", "validate"]),
    ("atm_withdrawal", "ATM withdrawal", "ATM withdrawals", "withdrawal_id", "Cash withdrawals at ATMs, including foreign ATM fees.", ["get", "list", "search", "summary", "history", "export"]),
    ("payment_limit", "payment limit", "payment limits", "customer_id", "Daily limits for transfers, bill pay and P2P payments.", ["get", "update", "history", "validate", "notify", "status"]),
    ("fx_alert", "FX rate alert", "FX rate alerts", "alert_id", "Notify the customer when an exchange rate reaches a target.", ["get", "list", "create", "update", "close", "notify"]),
]

# Hand-written tools that the labelled requests target, plus their close siblings (the near-duplicates that make
# selection hard: card_block vs card_freeze, dispute_open vs card_get / card_transaction_search, ...).
_GW_CARDS_EXTRA: list[tuple[str, str, list[str]]] = [
    ("card_block", "Permanently block a card that is lost or stolen. The card can never be used again; a replacement is ordered with card_replace. For a temporary hold use card_freeze.", ["card_last4", "reason"]),
    ("card_freeze", "Temporarily freeze a card (e.g. misplaced). Reversible with card_unfreeze. For lost or stolen cards use card_block.", ["card_last4"]),
    ("card_unfreeze", "Unfreeze a temporarily frozen card so it can be used again.", ["card_last4"]),
    ("card_replace", "Order a replacement card (new number) after a block, damage or expiry.", ["card_last4", "reason"]),
    ("card_activate", "Activate a newly received card.", ["card_last4"]),
    ("card_pin_reset", "Reset the PIN of a card and send the new PIN securely.", ["card_last4"]),
    ("card_spending_limit_update", "Change the daily spending or ATM cash limit on a debit card.", ["card_last4", "amount"]),
    ("dispute_open", "Open a dispute for a card charge the customer did not authorise or does not recognise (unknown merchant, duplicate, subscription after cancelling). Starts the chargeback process.", ["card_last4", "transaction_id", "amount", "reason"]),
    ("fx_rate_get", "Get today's exchange rate between two currencies (e.g. EUR to USD). Read-only.", ["from_currency", "to_currency"]),
    ("fx_quote_create", "Create a firm FX quote to convert an amount between currencies, valid for 60 seconds.", ["from_currency", "to_currency", "amount"]),
    ("fx_convert", "Execute a currency conversion between two of the customer's multi-currency balances.", ["from_currency", "to_currency", "amount"]),
    ("fx_rates_list", "List all supported currencies and their indicative rates against USD.", []),
]

_GW_SUPPORT: list[tuple[str, str, list[str]]] = [
    ("ticket_create", "Create a customer support ticket for a problem that needs follow-up by the support team (app errors, complaints, service issues).", ["customer_id", "subject", "description", "priority"]),
    ("ticket_get", "Get one support ticket by id with its status and notes.", ["ticket_id"]),
    ("ticket_list", "List a customer's open and closed support tickets.", ["customer_id"]),
    ("ticket_update", "Add a note to or change the priority of an existing support ticket.", ["ticket_id", "changes"]),
    ("ticket_close", "Close a resolved support ticket.", ["ticket_id", "reason"]),
    ("callback_schedule", "Schedule a phone callback from a specialist (mortgage advisor, fraud team, card services) at a requested time.", ["customer_id", "team", "preferred_time"]),
    ("complaint_register", "Register a formal regulatory complaint (starts the 15-business-day complaint clock).", ["customer_id", "description"]),
    ("feedback_submit", "Record customer feedback or a satisfaction score about an interaction.", ["customer_id", "score", "comment"]),
    ("faq_search", "Search the help-centre FAQ articles for an answer.", ["query"]),
    ("agent_handoff", "Transfer the live chat to a human support agent.", ["customer_id", "summary"]),
]


def _gw_schema(params: list[str]) -> dict[str, Any]:
    props = {p: dict(_GW_PARAMS.get(p) or {"type": "string", "description": p.replace("_", " ")}) for p in params}
    return {"type": "object", "properties": props, "required": [p for p in params if p not in _GW_OPTIONAL]}


def _gw_generated(entities: list[tuple]) -> list[dict[str, Any]]:
    out = []
    for ent, noun, plural, id_param, hint, verbs in entities:
        for v in verbs:
            tmpl, extra = _GW_VERBS[v]
            params = [] if v == "search" else (["customer_id"] if v in ("list", "summary", "create") else [id_param])
            params = list(dict.fromkeys(params + extra))
            out.append({"name": f"{ent}_{v}", "description": f"{tmpl.format(noun=noun, plural=plural)} {hint}",
                        "inputSchema": _gw_schema(params)})
    return out


def bank_tool_catalog() -> dict[str, list[dict[str, Any]]]:
    """The AnyCompany Bank tool catalog behind the M02 AgentCore Gateway: {target name: [tool definitions]}.

    3 targets = 3 back-end systems (slide 46): CoreBanking 175 · CardsPayments 150 · CustomerSupport 10 = 335 tools.
    Each tool is a Gateway ToolDefinition {name, description, inputSchema}; the gateway exposes it to MCP clients as
    "<target>___<name>" (max 46 chars, under Bedrock's 64-char tool-name limit). Generated from entity x verb, so the
    catalog is realistic in the way that matters: many near-duplicates (account_statement_get vs _export,
    card_block vs card_freeze, customer_address_get vs _update). Deterministic; a fresh copy on every call.
    """
    cards = _gw_generated(_GW_CARDS) + [{"name": n, "description": d, "inputSchema": _gw_schema(p)}
                                        for n, d, p in _GW_CARDS_EXTRA]
    cat = {"CoreBanking": _gw_generated(_GW_CORE)[:175],
           "CardsPayments": cards[:150],
           "CustomerSupport": [{"name": n, "description": d, "inputSchema": _gw_schema(p)} for n, d, p in _GW_SUPPORT]}
    for target, tools in cat.items():
        names = [t["name"] for t in tools]
        assert len(names) == len(set(names)) == TOOL_TARGET_SIZES[target], (target, len(names))
        assert all(len(f"{target}___{n}") <= 64 for n in names)
    return cat


# 10 labelled requests -> the ONE correct gateway tool ("<target>___<tool>"). Used for the M02 §9 accuracy demo:
# Nova 2 Lite with all 335 tools 20/20; Nova Micro with all tools 16/30 (7 wrong picks among near-duplicates) vs
# 23/30 with the search top-10 (test account, 2026-09-26). Requests 1, 4, 5 and 8 hit near-duplicate pairs.
LABELLED_TOOL_REQUESTS: list[tuple[str, str]] = [
    ("My card ending 4417 was stolen last night. Please block it for good.", "CardsPayments___card_block"),
    ("The mobile app crashes every time I upload a cheque photo. Please log this with support so someone follows up.",
     "CustomerSupport___ticket_create"),
    ("What is today's exchange rate from EUR to USD?", "CardsPayments___fx_rate_get"),
    ("There's a $89.99 charge from StreamFlix on card 4417 that I never authorised. I want to dispute it.",
     "CardsPayments___dispute_open"),
    ("How much would a $15,000 personal loan over 36 months cost me per month?", "CoreBanking___personal_loan_quote"),
    ("I moved. Change my mailing address to 12 Harbour St, Seattle WA 98101. I'm CUST-1002.",
     "CoreBanking___customer_address_update"),
    ("Set up a monthly standing order of $500 to my landlord from my checking account. I'm CUST-1001.",
     "CoreBanking___standing_order_create"),
    ("I think I left my debit card 7731 at a restaurant. Can you put it on hold temporarily while I look for it?",
     "CardsPayments___card_freeze"),
    ("I need my March 2026 account statement as a PDF. Statement id STM-1001-2026-03.",
     "CoreBanking___account_statement_export"),
    ("Can a mortgage advisor call me back tomorrow at 9am? I'm CUST-1003.", "CustomerSupport___callback_schedule"),
]


# --------------------------------------------------------------------------------------
# Operations log haystack (needle-in-a-haystack filler for M02 §2): card-processor AUTH lines, branch MEMOs,
# POLICY excerpts and ATM INCIDENTs. Reserved for needles (never in the filler): NOTICE lines, the words
# "wire-transfer cutoff", and INCIDENT lines for the "Harbor Street" branch. Nova 2 Lite retrieved a planted
# NOTICE 38/38 times from 8k to 1.04M tokens (research, 2026-09-26).
# --------------------------------------------------------------------------------------
OPS_LOG_CHARS_PER_TOKEN = 3.11      # Nova 2 Lite, measured on this log at 40k and 400k chars (3.129 / 3.111)

_OPS_BRANCHES = ["Queen Anne", "Ballard", "Capitol Hill", "Fremont", "Bellevue Square", "Redmond Town Center",
                 "Tacoma Dome", "Everett Marina", "Harbor Street", "Kirkland Waterfront", "Renton Landing",
                 "Issaquah Highlands"]
_OPS_MERCHANTS = [("Safeway", "5411"), ("Blue Bottle Coffee", "5814"), ("Amazon.com", "5942"), ("Shell", "5541"),
                  ("REI", "5941"), ("Uber", "4121"), ("Costco", "5300"), ("Walgreens", "5912"),
                  ("Pike Place Chowder", "5812"), ("Sound Transit", "4111"), ("Alaska Airlines", "3000"),
                  ("Home Depot", "5200"), ("Trader Joe's", "5411"), ("Chevron", "5541"), ("Apple Store", "5732")]
_OPS_POLICY = [
    "Card disputes must be filed within {n} days of the statement date. A provisional credit is issued within 10 business days while the merchant is contacted, and the customer is notified by their preferred channel.",
    "Unauthorized card-not-present activity requires an immediate block, a reissue with a new card number, and a fraud case in case management. Customer liability for confirmed unauthorized charges is zero.",
    "Personal loan applications require a pay stub, a bank statement and a government ID. Applications within {n} points of the product minimum credit score are routed to manual review.",
    "Debt-to-income is computed including the proposed payment. Applications with DTI between 0.40 and 0.43 go to manual review; above 0.43 they are declined with an adverse-action notice.",
    "Know-your-customer refresh is due every {n} months for Premier customers and every 36 months for Everyday customers, or immediately after a change of legal name or address.",
    "Cash transactions above 10,000 USD in one business day require a Currency Transaction Report. Structuring indicators are escalated to the BSA officer within {n} business days.",
    "Overdraft fees are waived for the first occurrence in any rolling {n}-month period. Hardship waivers require a supervisor note in the customer record.",
    "Safe deposit box access requires two forms of identification and a signature card match. Drilling requests follow the 90-day unpaid-rent process and require two witnesses.",
    "Mobile check deposits above {n},000 USD are held for two business days. Deposits flagged by the image-quality check must be re-captured before 9 PM local time.",
    "Travel notices are optional. Card-present transactions abroad are scored by the risk engine; card-not-present bursts across multiple countries within {n} hours trigger an automatic hold.",
]
_OPS_MEMO = [
    "teller drawer reconciliation completed; variance USD {a}; supervisor sign-off by staff ID {id}.",
    "night-drop bags processed: {n}; one bag held for dual-control recount; no discrepancies after recount.",
    "coin machine serviced by vendor; next service window in {n} days; lobby signage updated.",
    "{n} new checking accounts opened; {m} debit cards issued instant-print; two KYC exceptions escalated.",
    "customer complaint logged about wait times ({n} min peak); staffing adjusted for Friday afternoon.",
    "branch alarm test completed with the monitoring center; response time {n} seconds; log filed.",
    "loan officer hosted {n} first-time homebuyer consultations; three pre-qualification letters issued.",
    "ATM #{atm} cash cassette replenished to USD {a}; reject bin emptied; journal uploaded.",
    "shredding vendor pickup completed: {n} consoles; certificate of destruction filed.",
    "ADA compliance walk-through completed; ramp handrail repair ticket #{id} opened with facilities.",
]
_OPS_INCIDENT_BRANCHES = [b for b in _OPS_BRANCHES if b != "Harbor Street"]   # Harbor Street outages = needles only


def _ops_log_lines(seed: int):
    """Endless, deterministic stream of operations-log lines starting 2026-06-01 07:00:00."""
    rnd = _random.Random(seed)
    t = _dt.datetime(2026, 6, 1, 7, 0, 0)
    while True:
        t += _dt.timedelta(seconds=rnd.randint(5, 55))
        kind, ts = rnd.random(), t.strftime("%Y-%m-%d %H:%M:%S")
        if kind < 0.62:
            m, mcc = rnd.choice(_OPS_MERCHANTS)
            amt = round(rnd.uniform(3, 480), 2)
            res = "APPROVED" if rnd.random() > 0.04 else rnd.choice(
                ["DECLINED insufficient_funds", "DECLINED risk_score", "DECLINED expired_card"])
            yield (f"{ts} AUTH card=****{rnd.randint(1000, 9999)} merchant=\"{m}\" mcc={mcc} amount=USD {amt} "
                   f"entry={rnd.choice(['chip', 'contactless', 'ecommerce', 'credential_on_file'])} result={res} "
                   f"risk={rnd.randint(1, 60)}")
        elif kind < 0.84:
            b, tmpl = rnd.choice(_OPS_BRANCHES), rnd.choice(_OPS_MEMO)
            txt = tmpl.format(a=f"{rnd.uniform(0, 40000):,.2f}", id=rnd.randint(1000, 9999), n=rnd.randint(2, 45),
                              m=rnd.randint(1, 9), atm=rnd.randint(100, 999))
            yield f"{ts} MEMO branch=\"{b}\": {txt}"
        elif kind < 0.93:
            yield (f"{ts} POLICY excerpt §{rnd.randint(1, 30)}.{rnd.randint(1, 20)}: "
                   + rnd.choice(_OPS_POLICY).format(n=rnd.randint(2, 60)))
        else:
            b = rnd.choice(_OPS_INCIDENT_BRANCHES)
            fault = rnd.choice(["card reader fault", "network timeout", "cash dispenser jam", "receipt printer fault"])
            yield (f"{ts} INCIDENT branch=\"{b}\": ATM #{rnd.randint(100, 999)} outage reported; {fault}; "
                   f"restored after {rnd.randint(12, 240)} min.")


def ops_log_haystack(target_tokens: int, needles: _Iterable[tuple[float, str]] = (), seed: int = 11) -> str:
    """A realistic AnyCompany Bank operations log of about `target_tokens` Nova 2 Lite tokens, with needles planted.

    Args:
        target_tokens: Size of the returned text in Nova 2 Lite tokens (at OPS_LOG_CHARS_PER_TOKEN = 3.11 chars per
            token; the needles count towards it). Measure the exact figure with `usage.inputTokens` or mc.count_tokens.
        needles: (depth, line) pairs. depth 0.0 = first line, 0.5 = middle, 1.0 = last line; each line is inserted
            at that fraction of the log's lines. Write needles in the log's own style, e.g.
            '2026-06-14 10:02:37 NOTICE branch="Harbor Street": ... wire-transfer cutoff ... is 3:47 PM Pacific.'
        seed: Filler seed. The same (target_tokens, needles, seed) always returns the same text, and a smaller
            target is a prefix of a larger one (before needles are inserted).
    """
    needles = [(min(1.0, max(0.0, float(d))), str(text)) for d, text in needles]
    budget = int(target_tokens * OPS_LOG_CHARS_PER_TOKEN) - sum(len(t) + 1 for _, t in needles)
    body, size = [], 0
    for line in _ops_log_lines(seed):
        if size + len(line) + 1 > budget:
            break
        body.append(line)
        size += len(line) + 1
    for depth, text in sorted(needles, key=lambda x: -x[0]):      # deepest first, so earlier indexes stay valid
        body.insert(round(depth * len(body)), text)
    return "\n".join(body)
