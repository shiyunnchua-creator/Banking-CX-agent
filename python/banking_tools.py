"""Tools layer: the actions Mia can take against a (mocked) banking backend.

Each function is a normal Python function with @tool metadata so the LLM can request it.
In production these wrap real systems:
  - get_customer / verify_identity      -> core banking + KYC / identity service
  - get_transactions / get_transaction  -> card processor / ledger
  - raise_dispute / issue_provisional_credit / freeze_card -> disputes & cards platform
  - escalate_to_human                   -> case management / queue
  - *_card_* / *_loan_*                  -> originations / lending decisioning

Two governance rules are enforced *in the tools themselves* so they cannot be skipped:
  1. Account-specific reads require a prior successful verify_identity (this session).
  2. State-changing actions require confirmed=True (the explicit customer "yes").
Every call appends a record to the audit trail.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from langchain_core.tools import tool

from audit import AUDIT

DATA_DIR = Path(os.getenv("MERIDIAN_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

_customers = {c["customer_id"]: c for c in json.loads((DATA_DIR / "customers.json").read_text())}
_transactions = {t["txn_id"]: t for t in json.loads((DATA_DIR / "transactions.json").read_text())}
_knowledge = json.loads((DATA_DIR / "bank_knowledge.json").read_text())
_products = json.loads((DATA_DIR / "products.json").read_text())

# --- Session context (set at the start of a conversation) -------------------
# Keeps verification state and conversation id available to every tool so the
# audit trail and the verify-before-act gate work without threading state through
# each call. One session = one conversation in this POC.
SESSION = {
    "conversation_id": "CONV-LOCAL",
    "verified_customers": set(),
    "location": "unknown",
}


def start_session(conversation_id: str) -> None:
    SESSION["conversation_id"] = conversation_id
    SESSION["verified_customers"] = set()
    SESSION["location"] = "unknown"


def _is_verified(customer_id: str) -> bool:
    return customer_id in SESSION["verified_customers"]


def _log(action, outcome, category="general", confidence_level="n/a",
         customer_id=None, status="logged"):
    return AUDIT.log(
        conversation_id=SESSION["conversation_id"],
        action=action, outcome=outcome, category=category,
        confidence_level=confidence_level, customer_id=customer_id,
        location=SESSION["location"], status=status,
    )


# ===========================================================================
# Identity & read-only tools
# ===========================================================================

@tool
def verify_identity(customer_id: str, name: str, factor_type: str, factor_value: str) -> str:
    """Verify a customer before any account-specific action.

    Requires the customer's full name PLUS one factor: factor_type is one of
    'card_last4', 'dob' (YYYY-MM-DD), or 'phone_last4', with the matching value.
    On success the customer is marked verified for this conversation.
    """
    cust = _customers.get(customer_id)
    if not cust:
        _log("verify_identity", "no such customer", category="security",
             customer_id=customer_id, status="rejected")
        return f"No customer found with id {customer_id}."

    name_ok = name.strip().lower() == cust["name"].lower()
    factor_ok = False
    if factor_type == "dob":
        factor_ok = factor_value == cust["dob"]
    elif factor_type == "phone_last4":
        factor_ok = factor_value == cust["phone_last4"]
    elif factor_type == "card_last4":
        factor_ok = any(factor_value == c["last4"] for c in cust["cards"])

    if name_ok and factor_ok:
        SESSION["verified_customers"].add(customer_id)
        SESSION["location"] = cust["home_location"]
        _log("verify_identity", "verified", category="security",
             customer_id=customer_id, status="resolved")
        return f"Identity verified for {cust['name']}. You may proceed with account actions."

    _log("verify_identity", "verification failed", category="security",
         customer_id=customer_id, status="rejected")
    return "Verification failed. Do not disclose account details or take any action."


@tool
def get_customer(customer_id: str) -> str:
    """Return a customer's profile (accounts and cards, card numbers masked).

    Requires a successful verify_identity first; otherwise returns a masked stub.
    """
    cust = _customers.get(customer_id)
    if not cust:
        return f"No customer found with id {customer_id}."
    if not _is_verified(customer_id):
        _log("get_customer", "blocked: not verified", customer_id=customer_id, status="rejected")
        return "Customer not verified yet. Run verify_identity before reading account data."

    _log("get_customer", "profile read", customer_id=customer_id)
    safe = {
        "customer_id": cust["customer_id"],
        "name": cust["name"],
        "home_location": cust["home_location"],
        "segment": cust["segment"],
        "accounts": cust["accounts"],
        "cards": [{"card_id": c["card_id"], "type": c["type"], "last4": c["last4"],
                   "status": c["status"]} for c in cust["cards"]],
    }
    return json.dumps(safe, indent=2)


@tool
def get_transactions(customer_id: str) -> str:
    """List recent transactions for a verified customer (newest first)."""
    if not _is_verified(customer_id):
        _log("get_transactions", "blocked: not verified", customer_id=customer_id, status="rejected")
        return "Customer not verified yet. Run verify_identity first."
    txns = [t for t in _transactions.values() if t["customer_id"] == customer_id]
    txns.sort(key=lambda t: t["date"], reverse=True)
    _log("get_transactions", f"{len(txns)} transactions read", customer_id=customer_id)
    if not txns:
        return "No transactions found."
    return json.dumps(txns, indent=2)


@tool
def get_transaction(txn_id: str) -> str:
    """Return full details for one transaction, including risk flags."""
    txn = _transactions.get(txn_id)
    if not txn:
        return f"No transaction found with id {txn_id}."
    if not _is_verified(txn["customer_id"]):
        _log("get_transaction", "blocked: not verified", customer_id=txn["customer_id"], status="rejected")
        return "Customer not verified yet. Run verify_identity first."
    _log("get_transaction", f"read {txn_id}", customer_id=txn["customer_id"])
    return json.dumps(txn, indent=2)


@tool
def search_bank_knowledge(query: str) -> str:
    """Search Meridian's internal knowledge base (policies, products, process)."""
    q = query.lower()
    matches = []
    for item in _knowledge:
        haystack = " ".join([item["title"], item["topic"], item["content"]]).lower()
        if q in haystack or any(tok in haystack for tok in q.split()):
            matches.append(item)
    _log("search_bank_knowledge", f"query='{query}' hits={len(matches)}")
    if not matches:
        return f"No internal knowledge match for: {query}"
    return "\n\n".join(
        f"Title: {m['title']}\nTopic: {m['topic']}\nContent: {m['content']}"
        for m in matches[:3]
    )


@tool
def check_dispute_policy(scenario: str) -> str:
    """Return the dispute policy and thresholds relevant to a scenario.

    scenario is a short description, e.g. 'duplicate charge', 'unrecognised
    subscription', or 'suspected fraud foreign'.
    """
    relevant = [m for m in _knowledge if m["topic"] == "disputes"]
    _log("check_dispute_policy", f"scenario='{scenario}'", category="charge_dispute")
    return "\n\n".join(f"{m['title']}: {m['content']}" for m in relevant)


# ===========================================================================
# State-changing tools (require confirmed=True)  -- charge disputes
# ===========================================================================

@tool
def raise_dispute(txn_id: str, reason: str, confirmed: bool = False) -> str:
    """Raise a formal dispute on a transaction. Requires confirmed=True (the
    customer's explicit 'yes') and a verified customer."""
    txn = _transactions.get(txn_id)
    if not txn:
        return f"No transaction found with id {txn_id}."
    if not _is_verified(txn["customer_id"]):
        _log("raise_dispute", "blocked: not verified", category="charge_dispute",
             customer_id=txn["customer_id"], status="rejected")
        return "Cannot raise a dispute: customer not verified."
    if not confirmed:
        _log("raise_dispute", "blocked: not confirmed", category="charge_dispute",
             customer_id=txn["customer_id"], status="rejected")
        return ("Confirmation required. Restate the transaction, merchant and amount to the "
                "customer and call again with confirmed=True only after an explicit 'yes'.")
    case_id = "DSP-" + txn_id.split("-")[-1]
    _log("raise_dispute", f"dispute {case_id} opened on {txn_id}: {reason}",
         category="charge_dispute", confidence_level="high",
         customer_id=txn["customer_id"], status="resolved")
    return f"Dispute {case_id} opened on {txn_id} for £{txn['amount']} at {txn['merchant']}. Reason: {reason}."


@tool
def issue_provisional_credit(txn_id: str, amount: float, confirmed: bool = False) -> str:
    """Issue provisional credit for a disputed charge. Agent-tier limit is £100;
    larger amounts must be escalated. Requires confirmed=True and a verified customer."""
    txn = _transactions.get(txn_id)
    if not txn:
        return f"No transaction found with id {txn_id}."
    if not _is_verified(txn["customer_id"]):
        _log("issue_provisional_credit", "blocked: not verified", category="charge_dispute",
             customer_id=txn["customer_id"], status="rejected")
        return "Cannot issue credit: customer not verified."
    if amount > 100:
        _log("issue_provisional_credit", f"blocked: £{amount} over agent limit",
             category="charge_dispute", customer_id=txn["customer_id"], status="rejected")
        return ("Amount exceeds the £100 agent-tier limit. Escalate to the Disputes team "
                "for human approval.")
    if not confirmed:
        _log("issue_provisional_credit", "blocked: not confirmed", category="charge_dispute",
             customer_id=txn["customer_id"], status="rejected")
        return "Confirmation required. Get an explicit 'yes' then call again with confirmed=True."
    _log("issue_provisional_credit", f"£{amount} credited for {txn_id}",
         category="charge_dispute", confidence_level="high",
         customer_id=txn["customer_id"], status="resolved")
    return f"Provisional credit of £{amount} applied for {txn_id}. Final outcome within 10 working days."


@tool
def freeze_card(card_id: str, confirmed: bool = False) -> str:
    """Freeze a card to contain suspected fraud. Requires confirmed=True."""
    owner = next((c for c in _customers.values()
                  if any(card["card_id"] == card_id for card in c["cards"])), None)
    if not owner:
        return f"No card found with id {card_id}."
    if not confirmed:
        _log("freeze_card", "blocked: not confirmed", category="charge_dispute",
             customer_id=owner["customer_id"], status="rejected")
        return "Confirmation required before freezing a card. Confirm with the customer, then confirmed=True."
    _log("freeze_card", f"card {card_id} frozen", category="charge_dispute",
         confidence_level="low", customer_id=owner["customer_id"], status="resolved")
    return f"Card {card_id} is now frozen. A replacement can be issued by the Fraud team."


@tool
def escalate_to_human(team: str, summary: str, reference_id: str = "", confidence_level: str = "n/a") -> str:
    """Hand a compiled case to a human team: 'disputes', 'fraud', or 'lending'.

    summary should be the complete case packet so the human does not have to re-ask
    the customer. Always include the reference_id and confidence_level.
    """
    routes = {
        "disputes": "Priya (Disputes Lead)",
        "fraud": "Marcus (Fraud Duty Officer)",
        "lending": "Sara (Lending Officer)",
    }
    owner = routes.get(team.lower())
    if not owner:
        return f"Unknown team '{team}'. Choose one of: {', '.join(routes)}."
    rec = _log("escalate_to_human", f"routed to {owner}: {summary[:120]}",
               confidence_level=confidence_level, status="escalated")
    return (f"Escalated to {owner}. Case ref {reference_id or rec.reference_id}, "
            f"conversation {SESSION['conversation_id']}, confidence={confidence_level}. "
            f"Packet handed over; the customer does not need to repeat anything.")


# ===========================================================================
# Scaffolded journeys: credit card applications & loan enquiries
# (Stubs with realistic shapes. Build these out the same way as disputes.)
# ===========================================================================

@tool
def get_card_products() -> str:
    """List Meridian credit card products with fees, APR and eligibility."""
    _log("get_card_products", "card catalogue read", category="credit_card_application")
    return json.dumps(_products["credit_cards"], indent=2)


@tool
def check_card_eligibility(customer_id: str, annual_income_gbp: float) -> str:
    """Soft eligibility indication for credit cards (NOT a credit decision).

    Scaffold: returns indicative eligibility only. A real credit decision is made by
    the Lending team / decisioning engine, never by the agent.
    """
    if not _is_verified(customer_id):
        return "Customer not verified yet. Run verify_identity first."
    eligible = [p for p in _products["credit_cards"] if annual_income_gbp >= p["min_income_gbp"]]
    _log("check_card_eligibility", f"income £{annual_income_gbp}: {len(eligible)} products",
         category="credit_card_application", confidence_level="medium", customer_id=customer_id)
    return json.dumps({"indicative_eligible": [p["name"] for p in eligible],
                       "note": "Indicative only. Final decision requires Lending review."}, indent=2)


@tool
def start_card_application(customer_id: str, product_id: str, confirmed: bool = False) -> str:
    """Begin a credit card application (scaffold). Requires confirmed=True. The final
    decision always escalates to the Lending team."""
    if not _is_verified(customer_id):
        return "Customer not verified yet. Run verify_identity first."
    if not confirmed:
        return "Confirmation required. Confirm the product and terms, then confirmed=True."
    _log("start_card_application", f"application started for {product_id}",
         category="credit_card_application", confidence_level="medium",
         customer_id=customer_id, status="escalated")
    return (f"Application for {product_id} started and routed to the Lending team for a credit decision. "
            f"[SCAFFOLD] Build out KYC refresh, affordability, and decisioning here.")


@tool
def get_loan_products() -> str:
    """List Meridian personal/homeowner loan products and ranges."""
    _log("get_loan_products", "loan catalogue read", category="loan_enquiry")
    return json.dumps(_products["loans"], indent=2)


@tool
def estimate_repayment(amount_gbp: float, term_months: int, apr: float) -> str:
    """Indicative monthly repayment for a loan (soft quote, no credit impact)."""
    r = apr / 100 / 12
    if r == 0:
        monthly = amount_gbp / term_months
    else:
        monthly = amount_gbp * r / (1 - (1 + r) ** (-term_months))
    _log("estimate_repayment", f"£{amount_gbp} over {term_months}m @ {apr}%",
         category="loan_enquiry", confidence_level="high")
    return json.dumps({"monthly_repayment_gbp": round(monthly, 2),
                       "total_repayable_gbp": round(monthly * term_months, 2),
                       "note": "Indicative only. Soft search; not a formal offer."}, indent=2)


@tool
def start_loan_enquiry(customer_id: str, amount_gbp: float, term_months: int, confirmed: bool = False) -> str:
    """Register a formal loan enquiry (scaffold). Requires confirmed=True. Affordability
    and any formal offer are decided by the Lending team."""
    if not _is_verified(customer_id):
        return "Customer not verified yet. Run verify_identity first."
    if not confirmed:
        return "Confirmation required. Confirm amount and term, then confirmed=True."
    _log("start_loan_enquiry", f"enquiry £{amount_gbp}/{term_months}m",
         category="loan_enquiry", confidence_level="medium",
         customer_id=customer_id, status="escalated")
    return (f"Loan enquiry registered and routed to the Lending team. "
            f"[SCAFFOLD] Build out affordability assessment and formal offer here.")


# Tool groups -----------------------------------------------------------------
DISPUTE_TOOLS = [
    verify_identity, get_customer, get_transactions, get_transaction,
    search_bank_knowledge, check_dispute_policy,
    raise_dispute, issue_provisional_credit, freeze_card, escalate_to_human,
]

CARD_TOOLS = [get_card_products, check_card_eligibility, start_card_application]
LOAN_TOOLS = [get_loan_products, estimate_repayment, start_loan_enquiry]

ALL_TOOLS = DISPUTE_TOOLS + CARD_TOOLS + LOAN_TOOLS
