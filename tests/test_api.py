"""API and conversation state against Postgres (see conftest.py). Scripted fake LLM."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from care import tools
from care.api import create_app
from care.state import AGENT, BOT, Store
from test_core import FakeLLM, route_json

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MSISDN = "263772000001"


@pytest.fixture(autouse=True)
def _fresh_fakes():
    tools.reset_fakes()


@pytest.fixture
def make_client(db_url):
    """Each call is a separate worker: its own app and connection pool, same database."""
    stores = []

    def make(llm):
        stores.append(Store(db_url))
        return TestClient(create_app(token=TOKEN, store=stores[-1], llm=llm))

    yield make
    for s in stores:
        s.close()


def msg(message_id, text="What is my balance?", msisdn=MSISDN):
    return {"message_id": message_id, "msisdn": msisdn, "area": "harare-cbd", "text": text}


def test_answer_and_status(make_client):
    r = make_client(FakeLLM(route_json("balance"))).post("/messages", json=msg("wamid.1"), headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == BOT and body["route"] == "balance" and "3.20" in body["reply"]
    assert body["duplicate"] is False


def test_redelivered_message_is_not_processed_twice(make_client):
    llm = FakeLLM(route_json("fault_report"))
    c = make_client(llm)
    a = c.post("/messages", json=msg("wamid.1", "my calls keep dropping"), headers=AUTH).json()
    b = c.post("/messages", json=msg("wamid.1", "my calls keep dropping"), headers=AUTH).json()
    assert llm.calls == 1
    assert b["duplicate"] is True and b["reply"] == a["reply"]


def test_handoff_gives_conversation_to_agent_and_bot_stays_quiet(make_client):
    llm = FakeLLM(route_json("balance"))
    c = make_client(llm)
    first = c.post("/messages", json=msg("wamid.1", "I want a refund"), headers=AUTH).json()
    assert first["handoff"] is True and first["status"] == AGENT

    second = c.post("/messages", json=msg("wamid.2"), headers=AUTH).json()
    assert second["reply"] is None and second["route"] == "agent" and second["status"] == AGENT
    assert llm.calls == 0          # high-risk skips the model; agent-owned skips the bot


def test_release_hands_back_to_bot(make_client):
    c = make_client(FakeLLM(route_json("balance")))
    c.post("/messages", json=msg("wamid.1", "I want a refund"), headers=AUTH)
    assert c.post(f"/conversations/{MSISDN}/release", headers=AUTH).json()["status"] == BOT
    r = c.post("/messages", json=msg("wamid.2"), headers=AUTH).json()
    assert r["status"] == BOT and "3.20" in r["reply"]


def test_state_survives_restart(make_client):
    make_client(FakeLLM()).post("/messages", json=msg("wamid.1", "I want a refund"), headers=AUTH)
    llm = FakeLLM(route_json("balance"))
    c = make_client(llm)                                  # new worker, same database
    assert c.get(f"/conversations/{MSISDN}", headers=AUTH).json()["status"] == AGENT
    assert c.post("/messages", json=msg("wamid.1", "I want a refund"), headers=AUTH).json()["duplicate"] is True
    assert llm.calls == 0


def test_conversation_view_has_no_customer_text(make_client):
    c = make_client(FakeLLM(route_json("balance")))
    c.post("/messages", json=msg("wamid.1", "What is my balance? I'm Tendai"), headers=AUTH)
    view = c.get(f"/conversations/{MSISDN}", headers=AUTH)
    assert "Tendai" not in view.text and len(view.json()["messages"]) == 1


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_auth_required(make_client, headers):
    c = make_client(FakeLLM())
    assert c.post("/messages", json=msg("wamid.1"), headers=headers).status_code == 401
    assert c.get(f"/conversations/{MSISDN}", headers=headers).status_code == 401
    assert c.get("/health").status_code == 200


@pytest.mark.parametrize("override", [{"text": "x" * 4097}, {"text": ""}, {"msisdn": "0772abc"}, {"message_id": ""}])
def test_bad_input_rejected(make_client, override):
    llm = FakeLLM()
    r = make_client(llm).post("/messages", json={**msg("wamid.1"), **override}, headers=AUTH)
    assert r.status_code == 422 and llm.calls == 0


@pytest.mark.parametrize("missing", ["CARE_API_TOKEN", "CARE_DATABASE_URL"])
def test_app_refuses_to_start_without_config(monkeypatch, missing):
    monkeypatch.setenv("CARE_API_TOKEN", TOKEN)
    monkeypatch.setenv("CARE_DATABASE_URL", "postgresql://unused")
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match=missing):
        create_app(llm=FakeLLM())


# --- several workers -------------------------------------------------------------
class SlowLLM:
    """Thread-safe fake: classifies as balance after a pause, and tracks how many calls overlap."""
    def __init__(self, delay=0.3):
        self.delay, self.calls, self.active, self.max_active = delay, 0, 0, 0
        self._lock = threading.Lock()

    def __call__(self, messages, json_mode=False):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self._lock:
            self.active -= 1
        return route_json("balance")


def _parallel(*calls):
    with ThreadPoolExecutor(len(calls)) as pool:
        return [f.result() for f in [pool.submit(c) for c in calls]]


def test_one_customer_is_handled_one_message_at_a_time_across_workers(make_client):
    llm = SlowLLM()
    a, b = make_client(llm), make_client(llm)
    ra, rb = _parallel(lambda: a.post("/messages", json=msg("wamid.1"), headers=AUTH),
                       lambda: b.post("/messages", json=msg("wamid.2"), headers=AUTH))
    assert ra.status_code == rb.status_code == 200
    assert llm.calls == 2 and llm.max_active == 1


def test_different_customers_run_in_parallel(make_client):
    llm = SlowLLM()
    a, b = make_client(llm), make_client(llm)
    ra, rb = _parallel(lambda: a.post("/messages", json=msg("wamid.1"), headers=AUTH),
                       lambda: b.post("/messages", json=msg("wamid.2", msisdn="263772000002"), headers=AUTH))
    assert ra.status_code == rb.status_code == 200
    assert llm.max_active == 2


def test_same_message_raced_by_two_workers_is_processed_once(make_client):
    llm = SlowLLM()
    a, b = make_client(llm), make_client(llm)
    ra, rb = _parallel(lambda: a.post("/messages", json=msg("wamid.1"), headers=AUTH),
                       lambda: b.post("/messages", json=msg("wamid.1"), headers=AUTH))
    assert llm.calls == 1
    assert sorted([ra.json()["duplicate"], rb.json()["duplicate"]]) == [False, True]
    assert ra.json()["reply"] == rb.json()["reply"]


def test_release_waits_for_message_in_progress(db_url):
    """A release can't land mid-turn and then be overwritten by that turn's handoff."""
    a, b = Store(db_url), Store(db_url)
    try:
        def turn_that_hands_off():
            with a.turn(MSISDN) as t:
                t.claim("wamid.1")
                time.sleep(0.4)                     # the model call
                t.set_status(AGENT, "high_risk")

        def release_during_it():
            time.sleep(0.1)
            start = time.perf_counter()
            b.release(MSISDN)
            return time.perf_counter() - start

        _, waited = _parallel(turn_that_hands_off, release_during_it)
        assert waited > 0.2                         # blocked until the turn committed
        assert a.conversation(MSISDN).status == BOT # and was applied after it
    finally:
        a.close()
        b.close()


def test_crash_mid_message_releases_the_claim(db_url):
    """If handling fails before commit, the redelivery is processed from scratch."""
    store = Store(db_url)
    try:
        with pytest.raises(RuntimeError):
            with store.turn(MSISDN) as t:
                assert t.claim("wamid.1") is None
                raise RuntimeError("worker died")
        with store.turn(MSISDN) as t:
            assert t.claim("wamid.1") is None       # still new
    finally:
        store.close()
