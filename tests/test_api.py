"""API and conversation state against Postgres (see conftest.py). Scripted fake LLM."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import psycopg

from care import tools
from care.api import create_app
from care.state import AGENT, BOT, CLOSED, Store
from conftest import migrate
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
    assert body["duplicate"] is False and body["conversation_id"]


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
                t.hand_off("high_risk")

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


# --- conversation lifecycle ------------------------------------------------------
REFUND = "I want a refund"      # high-risk: hands off without calling the model


def post(c, message_id, text="What is my balance?", msisdn=MSISDN):
    r = c.post("/messages", json=msg(message_id, text, msisdn), headers=AUTH)
    assert r.status_code == 200, r.text
    return r.json()


def age(db_url, hours=25):
    """Make every open conversation look idle for `hours`."""
    with psycopg.connect(db_url) as conn:
        conn.execute("UPDATE conversations SET last_activity_at = now() - make_interval(hours => %s) "
                     "WHERE status <> 'closed'", (hours,))


def events(c, msisdn=MSISDN):
    return [e["type"] for e in c.get(f"/conversations/{msisdn}", headers=AUTH).json()["events"]]


def test_messages_continue_one_conversation(make_client):
    c = make_client(FakeLLM(route_json("balance")))
    a, b = post(c, "wamid.1"), post(c, "wamid.2")
    assert a["conversation_id"] == b["conversation_id"]
    assert len(c.get(f"/customers/{MSISDN}/conversations", headers=AUTH).json()) == 1


def test_idle_conversation_times_out_and_a_new_one_opens(make_client, db_url):
    c = make_client(FakeLLM(route_json("balance")))
    first = post(c, "wamid.1")
    age(db_url)
    second = post(c, "wamid.2")
    assert second["conversation_id"] != first["conversation_id"] and second["status"] == BOT
    old, new = sorted(c.get(f"/customers/{MSISDN}/conversations", headers=AUTH).json(), key=lambda x: x["opened_at"])
    assert (old["status"], old["close_reason"], new["status"]) == (CLOSED, "timeout", BOT)


def test_unpicked_handoff_is_abandoned_and_bot_answers_new_conversation(make_client, db_url):
    c = make_client(FakeLLM(route_json("balance")))
    post(c, "wamid.1", REFUND)
    age(db_url)
    r = post(c, "wamid.2")
    assert r["status"] == BOT and "3.20" in r["reply"]     # the old agent conversation no longer silences the bot
    old = c.get(f"/customers/{MSISDN}/conversations", headers=AUTH).json()[-1]
    assert old["close_reason"] == "abandoned"


def test_picked_up_handoff_that_goes_idle_is_a_timeout(make_client, db_url):
    c = make_client(FakeLLM())
    post(c, "wamid.1", REFUND)
    c.post(f"/conversations/{MSISDN}/pickup", json={"agent_id": "agent-7"}, headers=AUTH)
    age(db_url)
    store = Store(db_url)
    try:
        assert store.close_idle() == 1
        assert store.conversation(MSISDN).close_reason == "timeout"
    finally:
        store.close()


def test_pickup_records_agent_and_rejects_bot_conversations(make_client):
    c = make_client(FakeLLM(route_json("balance")))
    post(c, "wamid.1")
    assert c.post(f"/conversations/{MSISDN}/pickup", json={"agent_id": "a1"}, headers=AUTH).status_code == 409
    post(c, "wamid.2", REFUND)
    r = c.post(f"/conversations/{MSISDN}/pickup", json={"agent_id": "a1"}, headers=AUTH).json()
    assert r["agent_id"] == "a1" and r["picked_up_at"] and r["status"] == AGENT


def test_agent_actions_without_open_conversation_are_404(make_client):
    c = make_client(FakeLLM())
    for path, body in [("pickup", {"agent_id": "a1"}), ("release", None), ("close", {"reason": "resolved"})]:
        assert c.post(f"/conversations/{MSISDN}/{path}", json=body, headers=AUTH).status_code == 404
    assert c.get(f"/conversations/{MSISDN}", headers=AUTH).status_code == 404


def test_close_then_next_message_opens_new_conversation(make_client):
    c = make_client(FakeLLM(route_json("balance")))
    first = post(c, "wamid.1", REFUND)
    closed = c.post(f"/conversations/{MSISDN}/close", json={"reason": "agent_resolved"}, headers=AUTH).json()
    assert (closed["status"], closed["close_reason"]) == (CLOSED, "agent_resolved")
    second = post(c, "wamid.2")
    assert second["conversation_id"] != first["conversation_id"] and second["status"] == BOT


def test_release_of_bot_conversation_is_409_and_logs_nothing(make_client):
    c = make_client(FakeLLM(route_json("balance")))
    post(c, "wamid.1")
    assert c.post(f"/conversations/{MSISDN}/release", headers=AUTH).status_code == 409
    assert events(c) == ["opened"]


def test_close_rejects_system_reasons(make_client):
    c = make_client(FakeLLM(route_json("balance")))
    post(c, "wamid.1")
    assert c.post(f"/conversations/{MSISDN}/close", json={"reason": "timeout"}, headers=AUTH).status_code == 422


def test_event_log_follows_the_lifecycle(make_client):
    c = make_client(FakeLLM(route_json("balance")))
    post(c, "wamid.1")
    post(c, "wamid.2", REFUND)
    c.post(f"/conversations/{MSISDN}/pickup", json={"agent_id": "a1"}, headers=AUTH)
    c.post(f"/conversations/{MSISDN}/release", headers=AUTH)
    c.post(f"/conversations/{MSISDN}/close", json={"reason": "resolved"}, headers=AUTH)
    assert events(c) == ["opened", "handoff", "picked_up", "released", "closed"]
    view = c.get(f"/conversations/{MSISDN}", headers=AUTH).json()
    assert view["events"][1]["payload"] == {"reason": "high_risk"}
    assert view["events"][2]["payload"] == {"agent_id": "a1"}


def test_second_handoff_needs_a_new_pickup(make_client):
    c = make_client(FakeLLM(route_json("balance")))
    post(c, "wamid.1", REFUND)
    c.post(f"/conversations/{MSISDN}/pickup", json={"agent_id": "a1"}, headers=AUTH)
    c.post(f"/conversations/{MSISDN}/release", headers=AUTH)
    post(c, "wamid.2", REFUND)
    view = c.get(f"/conversations/{MSISDN}", headers=AUTH).json()
    assert view["status"] == AGENT and view["agent_id"] is None and view["picked_up_at"] is None


def test_message_rows_hold_decisions(make_client, db_url):
    c = make_client(FakeLLM(route_json("balance", conf=0.85)))
    post(c, "wamid.1", "What is my balance? I'm Tendai")
    m = c.get(f"/conversations/{MSISDN}", headers=AUTH).json()["messages"][0]
    assert m["intent"] == "balance" and abs(m["confidence"] - 0.85) < 1e-6 and m["latency_ms"] >= 0
    with psycopg.connect(db_url) as conn:
        dump = str(conn.execute("SELECT m.*, e.* FROM messages m, events e").fetchall())
    assert "Tendai" not in dump


def test_customer_first_message_race_opens_one_conversation(make_client, db_url):
    llm = SlowLLM()
    a, b = make_client(llm), make_client(llm)
    ra, rb = _parallel(lambda: post(a, "wamid.1"), lambda: post(b, "wamid.2"))
    assert ra["conversation_id"] == rb["conversation_id"]
    with psycopg.connect(db_url) as conn:
        assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM customers").fetchone()[0] == 1


def test_sweeper_closes_only_idle_and_waits_for_turns(db_url):
    a, b = Store(db_url), Store(db_url)
    try:
        with a.turn(MSISDN) as t:
            t.conversation
        with a.turn("263772000002") as t:
            t.conversation
        age(db_url)
        with a.turn("263772000003") as t:           # fresh
            t.conversation

        # A message arrives for an idle customer while the sweep runs: the turn
        # rolls the conversation over itself, and the sweep must not double-close.
        def slow_turn():
            with b.turn(MSISDN) as t:
                t.conversation
                time.sleep(0.4)

        def sweep_during_it():
            time.sleep(0.1)
            return a.close_idle()

        _, closed = _parallel(slow_turn, sweep_during_it)
        assert closed == 1                           # only 263772000002; MSISDN was handled by the turn
        assert a.conversation("263772000003").status == BOT
        assert a.conversation(MSISDN).status == BOT  # the new conversation the turn opened
        assert a.conversation("263772000002").close_reason == "timeout"
    finally:
        a.close()
        b.close()


def test_migration_backfills_0001_data(db_url):
    migrate(db_url, "0001", down=True)
    try:
        with psycopg.connect(db_url) as conn:
            conn.execute("INSERT INTO conversations (msisdn, status, reason) VALUES "
                         "('263772000001', 'agent', 'high_risk'), ('263772000002', 'bot', 'released')")
            conn.execute("INSERT INTO messages (message_id, msisdn, route, handoff, blocked, reply) VALUES "
                         "('wamid.1', '263772000001', 'high_risk', true, '[]', 'x'), "
                         "('wamid.2', '263772000003', 'balance', false, '[]', 'y')")
    finally:
        migrate(db_url)
    store = Store(db_url)
    try:
        one, two, three = (store.conversation(m) for m in ("263772000001", "263772000002", "263772000003"))
        assert (one.status, one.handoff_reason) == (AGENT, "high_risk")
        assert (two.status, two.handoff_reason) == (BOT, None)
        assert three.status == BOT                   # had messages but no 0001 row
        assert [o.message_id for o in store.messages(one.conversation_id)] == ["wamid.1"]
        assert [e.type for e in store.events(one.conversation_id)] == ["opened"]
    finally:
        store.close()


def test_migration_downgrade_keeps_open_conversations(make_client, db_url):
    post(make_client(FakeLLM()), "wamid.1", REFUND)
    migrate(db_url, "0001", down=True)
    try:
        with psycopg.connect(db_url) as conn:
            assert conn.execute("SELECT msisdn, status, reason FROM conversations").fetchall() == \
                [(MSISDN, AGENT, "high_risk")]
    finally:
        migrate(db_url)
