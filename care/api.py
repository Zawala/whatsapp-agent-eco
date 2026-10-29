"""HTTP API in front of the assistant, called by the WhatsApp integration.

    CARE_API_TOKEN=... uvicorn care.api:create_app --factory

POST /messages handles one inbound message:
  - a message ID seen before gets the stored outcome back (duplicate=true)
  - if an agent owns the conversation, the bot doesn't reply (reply=null)
  - otherwise the assistant answers; any handoff gives the conversation to an agent
POST /conversations/{msisdn}/release hands it back to the bot.

msisdn and area are trusted: they must come from the WhatsApp layer, never from
message text. Every route except /health needs `Authorization: Bearer <token>`.
"""
import hmac
import os
from dataclasses import asdict
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import llm as llm_module
from .assistant import handle
from .state import AGENT, BOT, Outcome, Store
from .tools import Session

MSISDN = r"^\d{9,15}$"


class InboundMessage(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)
    msisdn: str = Field(pattern=MSISDN)
    area: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4096)   # WhatsApp's own limit for a text message


class MessageResult(BaseModel):
    message_id: str
    status: Literal["bot", "agent"]
    reply: str | None
    route: str
    handoff: bool
    blocked: list[str]
    duplicate: bool


def create_app(token: str | None = None, store: Store | None = None, llm=None) -> FastAPI:
    token = token or os.environ.get("CARE_API_TOKEN")
    if not token:
        raise RuntimeError("set CARE_API_TOKEN")
    store = store or Store(os.environ.get("CARE_DB", "care.db"))
    llm = llm or llm_module.chat

    def auth(authorization: str = Header(default="")) -> None:
        if not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "bad token")

    app = FastAPI(title="care-bot")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/messages", dependencies=[Depends(auth)])
    def post_message(msg: InboundMessage) -> MessageResult:
        with store.conversation_lock(msg.msisdn):
            if seen := store.outcome(msg.message_id):
                return _result(seen, store.conversation(msg.msisdn).status, duplicate=True)
            if store.conversation(msg.msisdn).status == AGENT:
                outcome = Outcome(msg.message_id, msg.msisdn, "agent", False, [], None)
            else:
                r = handle(msg.text, Session(msisdn=msg.msisdn, area=msg.area, message_id=msg.message_id), llm)
                outcome = Outcome(msg.message_id, msg.msisdn, r.route, r.handoff, r.blocked, r.text)
                if r.handoff:
                    store.set_status(msg.msisdn, AGENT, reason=",".join([r.route, *r.blocked]))
            store.record(outcome)
            return _result(outcome, store.conversation(msg.msisdn).status, duplicate=False)

    @app.get("/conversations/{msisdn}", dependencies=[Depends(auth)])
    def get_conversation(msisdn: str) -> dict:
        return {**asdict(store.conversation(msisdn)), "messages": [asdict(o) for o in store.history(msisdn)]}

    @app.post("/conversations/{msisdn}/release", dependencies=[Depends(auth)])
    def release(msisdn: str) -> dict:
        with store.conversation_lock(msisdn):
            store.set_status(msisdn, BOT, reason="released")
        return asdict(store.conversation(msisdn))

    return app


def _result(o: Outcome, status: str, duplicate: bool) -> MessageResult:
    return MessageResult(message_id=o.message_id, status=status, reply=o.reply, route=o.route,
                         handoff=o.handoff, blocked=o.blocked, duplicate=duplicate)
