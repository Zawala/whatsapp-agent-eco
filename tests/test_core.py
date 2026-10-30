"""Fast offline tests. No Ollama, no Phoenix."""
import json
from datetime import date
from pathlib import Path

import pytest

from care import assistant, checks, router, tools
from care.guard import INPUT_ENTITIES, redact
from care.knowledge import BUNDLES, FAQS
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


@pytest.mark.parametrize("text,amount", [
    ("four dollars fifty", 4.5), ("four dollars and fifty cents", 4.5), ("a dollar and a half", 1.5),
    ("half a dollar", 0.5), ("fifty cents", 0.5), ("50 cents", 0.5), ("4 dollars 50", 4.5),
    ("four point five dollars", 4.5), ("twenty dollars", 20.0), ("forty-five dollars", 45.0),
    ("nineteen dollars ninety nine", 19.99), ("one hundred dollars", 100.0), ("a twenty dollar bundle", 20.0),
    ("2 dollars", 2.0), ("4.50$", 4.5), ("$4.50", 4.5), ("USD 4.50", 4.5), ("4.50 USD", 4.5),
])
def test_prices_read_in_words_and_digits(text, amount):
    assert checks.prices_in(text) == {amount}
    assert not checks.check_prices(f"It costs {text}.", {amount})
    assert checks.check_prices(f"It costs {text}.", {amount + 1})


@pytest.mark.parametrize("text,amounts", [
    ("Dial *143# to buy.", set()), ("valid for 7 days", set()), ("5GB of data", set()),
    ("the dollar bundle", set()), ("a few dollars", set()),
    ("two dollars and one hundred minutes", {2.0}),   # "and" without "cents" isn't cents
    ("Costs 2 dollars, valid 7 days", {2.0}), ("2 dollars 7 days", {2.0}),
    ("four dollars fifty for 7 days", {4.5}),
])
def test_numbers_that_are_not_prices(text, amounts):
    assert checks.prices_in(text) == amounts


def test_amount_in_words_that_does_not_parse_is_blocked():
    assert checks.check_prices("It costs four five dollars.", {4.5}) == ["price_unparsed:four five dollars"]


def test_price_in_words_blocked_through_pipeline():
    fake = FakeLLM(route_json("bundle_info"), "The 5GB Weekly bundle costs four dollars.")
    r = assistant.handle("How much is the 5GB bundle?", session(), llm=fake)
    assert r.handoff and r.blocked == ["price_not_in_context:4.0"]


def test_catalogue_prices_match_text():
    """The price check allows price_usd, but the model reads text: they must agree."""
    for doc in BUNDLES:
        assert checks.prices_in(doc["text"]) == {doc["price_usd"]}, doc["id"]
    for doc in FAQS:
        assert checks.prices_in(doc["text"]) == set(), doc["id"]


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
    assert "5GB" in redact("How much is the 5GB bundle?", INPUT_ENTITIES)


@pytest.mark.parametrize("text,names", [
    ("Hi, I'm Tendai Moyo", ["Tendai", "Moyo"]),
    ("my name is rutendo", ["rutendo"]),
    ("Tatenda Ncube here, no signal", ["Tatenda", "Ncube"]),
    ("This is Farai", ["Farai"]),
    ("tendai's line is down", ["tendai"]),
    ("Hi I am Blessing Chari", ["Blessing", "Chari"]),       # not in the names list: caught by the introduction
    ("my name is tendai and my line is down", ["tendai"]),
])
def test_input_redaction_removes_zimbabwean_names(text, names):
    out = redact(text, INPUT_ENTITIES)
    assert not any(n in out for n in names), out


@pytest.mark.parametrize("text", [
    "I'm unable to buy data", "I'm having no signal", "This is urgent, no network", "I'm in Harare and have no signal",
    "Can I buy a gift bundle for my friend?", "How much is the 5GB bundle?",
])
def test_input_redaction_leaves_ordinary_messages_alone(text):
    assert redact(text, INPUT_ENTITIES) == text


def test_input_redaction_leaves_the_catalogue_alone():
    """Redaction runs before retrieval, so no catalogue word may look like a name."""
    for doc in BUNDLES + FAQS:
        for text in [doc["title"], doc["text"], *doc["keywords"]]:
            assert redact(text, INPUT_ENTITIES) == text, (doc["id"], text)
