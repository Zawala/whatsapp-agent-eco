"""Tool allowlist and fake backends.

The model may supply only the arguments a tool declares in `model_args`.
Identity (msisdn), area and the idempotency key come from the Session, never
from the model. Money-moving tools need a human approver. call_tool never
raises: every failure is a structured {"ok": False, "error": ...}.
"""
from dataclasses import dataclass
from typing import Callable


@dataclass
class Session:
    msisdn: str            # from the WhatsApp channel, never from message text
    area: str
    message_id: str        # WhatsApp message ID = idempotency key
    approved_by: str | None = None


@dataclass
class Tool:
    name: str
    model_args: set[str]
    needs_approval: bool
    fn: Callable[[Session, dict], dict]


# --- fake backends -----------------------------------------------------------
_BALANCES = {"263772000001": {"airtime_usd": 3.20, "data_mb": 850}}
_OUTAGES = {"harare-cbd": "Planned maintenance until 18:00 today."}
_TICKETS: dict[str, str] = {}        # message_id -> ticket id
_REVERSALS: list[str] = []


def reset_fakes() -> None:
    _TICKETS.clear()
    _REVERSALS.clear()


def _get_balance(s: Session, args: dict) -> dict:
    bal = _BALANCES.get(s.msisdn)
    return {"ok": True, **bal} if bal else {"ok": False, "error": "not_found"}


def _get_outage(s: Session, args: dict) -> dict:
    return {"ok": True, "area": s.area, "outage": _OUTAGES.get(s.area)}


def _create_ticket(s: Session, args: dict) -> dict:
    if s.message_id not in _TICKETS:
        _TICKETS[s.message_id] = f"TKT-{len(_TICKETS) + 1:05d}"
    return {"ok": True, "ticket_id": _TICKETS[s.message_id]}


def _reverse_transaction(s: Session, args: dict) -> dict:
    _REVERSALS.append(args["txn_id"])
    return {"ok": True, "reversed": args["txn_id"]}


TOOLS = {t.name: t for t in [
    Tool("get_balance", set(), False, _get_balance),
    Tool("get_outage", set(), False, _get_outage),
    Tool("create_ticket", {"category", "summary"}, False, _create_ticket),
    Tool("reverse_transaction", {"txn_id"}, True, _reverse_transaction),
]}


def call_tool(name: str, model_args: dict, session: Session) -> dict:
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "error": "unknown_tool"}
    if set(model_args) - tool.model_args:
        return {"ok": False, "error": "bad_args"}
    if tool.needs_approval and not session.approved_by:
        return {"ok": False, "error": "approval_required"}
    try:
        return tool.fn(session, model_args)
    except Exception:
        return {"ok": False, "error": "backend_error"}
