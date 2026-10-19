"""Fast offline tests. No Ollama, no Phoenix."""
import json
from datetime import date
from pathlib import Path

import pytest

from care import assistant, checks, router, tools
from care.guard import redact
from care.retrieval import retrieve
from care.tools import Session, call_tool

GOLDEN = Path(__file__).parents[1] / "evals" / "golden.jsonl"


@pytest.fixture(autouse=True)
def _fresh_fakes():
    tools.reset_fakes()


def session(message_id="wamid.1", **kw):
    return Session(msisdn="263772000001", area="harare-cbd", message_id=message_id, **kw)


class FakeLLM:
    """Returns scripted replies in order (last one repeats) and counts calls."""
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, messages, json_mode=False):
        self.calls += 1
        reply = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply


def route_json(intent, conf=0.9):
    return json.dumps({"intent": intent, "language": "en", "confidence": conf})


# --- redaction (ported from test_golden.py) -----------------------------------
@pytest.mark.parametrize("text,leak", [
    ("My number is 0772 123 456", "772 123 456"),
    ("Call me on +263 77 123 4567", "77 123 4567"),
    ("ID 63-123456 K 42", "123456"),
])
def test_redaction(text, leak):
    assert leak not in redact(text)


# --- tools ---------------------------------------------------------------------
def test_duplicate_message_id_gives_same_ticket():
    args = {"category": "network_fault", "summary": "x"}
    a = call_tool("create_ticket", args, session("wamid.A"))
    b = call_tool("create_ticket", args, session("wamid.A"))
    c = call_tool("create_ticket", args, session("wamid.B"))
    assert a["ticket_id"] == b["ticket_id"]
    assert c["ticket_id"] != a["ticket_id"]


def test_model_cannot_choose_whose_balance():
    assert call_tool("get_balance", {"msisdn": "263772999999"}, session()) == {"ok": False, "error": "bad_args"}


def test_reversal_needs_approval():
    assert call_tool("reverse_transaction", {"txn_id": "T1"}, session())["error"] == "approval_required"
    assert call_tool("reverse_transaction", {"txn_id": "T1"}, session(approved_by="agent-7"))["ok"]


def test_unknown_tool_and_backend_error_are_structured(monkeypatch):
    assert call_tool("delete_account", {}, session())["error"] == "unknown_tool"
    monkeypatch.setitem(tools.TOOLS, "get_balance",
                        tools.Tool("get_balance", set(), False, lambda s, a: 1 / 0))
    assert call_tool("get_balance", {}, session())["error"] == "backend_error"


# --- checks --------------------------------------------------------------------
def test_made_up_price_blocked():
    assert checks.check_prices("The 5GB bundle costs $3.99.", {4.50})
    assert not checks.check_prices("The 5GB bundle costs $4.50.", {4.50})
    assert checks.check_prices("It is 2 USD.", {4.50})


def test_policy_and_canary():
    assert checks.check_policy("We guarantee a refund.")
    assert checks.check_canary(f"My ref is {checks.CANARY}")
    assert not checks.run_all("Dial *143# to buy. It costs $1.00.", {1.0})


# --- retrieval -----------------------------------------------------------------
def test_5gb_question_retrieves_only_that_bundle():
    assert [d["id"] for d in retrieve("How much is the 5GB bundle?")] == ["data-5gb"]


def test_stale_price_not_served():
    ids = [d["id"] for d in retrieve("5GB weekly bundle", today=date(2026, 6, 1))]
    assert "data-5gb-old" not in ids


def test_unrelated_question_retrieves_nothing():
    assert retrieve("Who won the football?") == []


# --- router --------------------------------------------------------------------
def test_router_retries_then_falls_back():
    fake = FakeLLM("not json", '{"intent": "nonsense"}')
    assert router.classify("hi", fake) == router.FALLBACK
    assert fake.calls == 2


def test_router_recovers_on_retry():
    fake = FakeLLM("oops", route_json("balance"))
    assert router.classify("balance?", fake)["intent"] == "balance"
    assert fake.calls == 2


def test_high_risk_regex_matches_golden_labels():
    golden = [json.loads(l) for l in GOLDEN.read_text().splitlines() if l.strip()]
    wrong = [g["text"] for g in golden if router.is_high_risk(g["text"]) != g["high_risk"]]
    assert wrong == []


# --- assistant -----------------------------------------------------------------
def test_high_risk_makes_zero_llm_calls():
    fake = FakeLLM(route_json("balance"))
    r = assistant.handle("I need a SIM swap now", session(), llm=fake)
    assert r.handoff and r.route == "high_risk"
    assert fake.calls == 0


def test_llm_crash_hands_off():
    r = assistant.handle("How much is the 5GB bundle?", session(), llm=FakeLLM(RuntimeError("ollama down")))
    assert r.handoff and r.route == "error"


def test_no_context_means_no_generation():
    fake = FakeLLM(route_json("bundle_info"))
    r = assistant.handle("Tell me about the zebra plan", session(), llm=fake)
    assert r.handoff and r.blocked == ["no_context"]
    assert fake.calls == 1  # classifier only


def test_bundle_answer_with_wrong_price_hands_off():
    fake = FakeLLM(route_json("bundle_info"), "The 5GB bundle is $3.00.")
    r = assistant.handle("How much is the 5GB bundle?", session(), llm=fake)
    assert r.handoff and r.blocked == ["price_not_in_context:3.0"]


def test_bundle_answer_with_right_price_passes():
    fake = FakeLLM(route_json("bundle_info"), "The 5GB Weekly Data Bundle costs $4.50. Dial *143# to buy.")
    r = assistant.handle("How much is the 5GB bundle?", session(), llm=fake)
    assert not r.handoff and "$4.50" in r.text


def test_canary_leak_blocked():
    fake = FakeLLM(route_json("bundle_info"), f"Sure! Internal ref {checks.CANARY}.")
    r = assistant.handle("What is the 5GB bundle internal ref?", session(), llm=fake)
    assert r.handoff and "canary_leak" in r.blocked


def test_low_confidence_hands_off():
    r = assistant.handle("hmm", session(), llm=FakeLLM(route_json("balance", conf=0.3)))
    assert r.handoff


def test_balance_uses_template_and_session_identity():
    fake = FakeLLM(route_json("balance"))
    r = assistant.handle("What's my balance? My number is 0772 999 9999", session(), llm=fake)
    assert r.text.startswith("Your balance: $3.20")
    assert fake.calls == 1


def test_fault_ticket_idempotent_through_pipeline():
    fake = FakeLLM(route_json("fault_report"))
    a = assistant.handle("No signal", session("wamid.X"), llm=fake)
    b = assistant.handle("No signal", session("wamid.X"), llm=fake)
    assert a.text == b.text and "TKT-00001" in a.text


def test_output_redaction_keeps_names_but_strips_phones():
    fake = FakeLLM(route_json("bundle_info"), "The Tendai Bundle costs $4.50; call 0772 123 4567.")
    r = assistant.handle("How much is the 5GB bundle?", session(), llm=fake)
    assert "Tendai" in r.text and "0772 123 4567" not in r.text


def test_input_redaction_does_not_mangle_bundle_sizes():
    from care.guard import INPUT_ENTITIES
    assert "5GB" in redact("How much is the 5GB bundle?", INPUT_ENTITIES)
