"""API and conversation state. Offline: scripted fake LLM, SQLite in a temp dir."""
import pytest
from fastapi.testclient import TestClient

from care import tools
from care.api import create_app
from care.state import AGENT, BOT, Store
from test_core import FakeLLM, route_json

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _fresh_fakes():
    tools.reset_fakes()


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "care.db")


def client(db, llm):
    return TestClient(create_app(token=TOKEN, store=Store(db), llm=llm))


def msg(message_id, text="What is my balance?"):
    return {"message_id": message_id, "msisdn": "263772000001", "area": "harare-cbd", "text": text}


def test_answer_and_status(db):
    r = client(db, FakeLLM(route_json("balance"))).post("/messages", json=msg("wamid.1"), headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == BOT and body["route"] == "balance" and "3.20" in body["reply"]
    assert body["duplicate"] is False


def test_redelivered_message_is_not_processed_twice(db):
    llm = FakeLLM(route_json("fault_report"))
    c = client(db, llm)
    a = c.post("/messages", json=msg("wamid.1", "my calls keep dropping"), headers=AUTH).json()
    b = c.post("/messages", json=msg("wamid.1", "my calls keep dropping"), headers=AUTH).json()
    assert llm.calls == 1
    assert b["duplicate"] is True and b["reply"] == a["reply"]


def test_handoff_gives_conversation_to_agent_and_bot_stays_quiet(db):
    llm = FakeLLM(route_json("balance"))
    c = client(db, llm)
    first = c.post("/messages", json=msg("wamid.1", "I want a refund"), headers=AUTH).json()
    assert first["handoff"] is True and first["status"] == AGENT

    second = c.post("/messages", json=msg("wamid.2"), headers=AUTH).json()
    assert second["reply"] is None and second["route"] == "agent" and second["status"] == AGENT
    assert llm.calls == 0          # high-risk skips the model; agent-owned skips the bot


def test_release_hands_back_to_bot(db):
    c = client(db, FakeLLM(route_json("balance")))
    c.post("/messages", json=msg("wamid.1", "I want a refund"), headers=AUTH)
    assert c.post("/conversations/263772000001/release", headers=AUTH).json()["status"] == BOT
    r = c.post("/messages", json=msg("wamid.2"), headers=AUTH).json()
    assert r["status"] == BOT and "3.20" in r["reply"]


def test_state_survives_restart(db):
    client(db, FakeLLM()).post("/messages", json=msg("wamid.1", "I want a refund"), headers=AUTH)
    llm = FakeLLM(route_json("balance"))
    c = client(db, llm)                                   # new app, same database
    assert c.get("/conversations/263772000001", headers=AUTH).json()["status"] == AGENT
    assert c.post("/messages", json=msg("wamid.1", "I want a refund"), headers=AUTH).json()["duplicate"] is True
    assert llm.calls == 0


def test_conversation_view_has_no_customer_text(db):
    c = client(db, FakeLLM(route_json("balance")))
    c.post("/messages", json=msg("wamid.1", "What is my balance? I'm Tendai"), headers=AUTH)
    assert "Tendai" not in c.get("/conversations/263772000001", headers=AUTH).text


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_auth_required(db, headers):
    c = client(db, FakeLLM())
    assert c.post("/messages", json=msg("wamid.1"), headers=headers).status_code == 401
    assert c.get("/conversations/263772000001", headers=headers).status_code == 401
    assert c.get("/health").status_code == 200


@pytest.mark.parametrize("override", [{"text": "x" * 4097}, {"text": ""}, {"msisdn": "0772abc"}, {"message_id": ""}])
def test_bad_input_rejected(db, override):
    llm = FakeLLM()
    r = client(db, llm).post("/messages", json={**msg("wamid.1"), **override}, headers=AUTH)
    assert r.status_code == 422 and llm.calls == 0


def test_app_refuses_to_start_without_token(db, monkeypatch):
    monkeypatch.delenv("CARE_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        create_app(store=Store(db), llm=FakeLLM())
