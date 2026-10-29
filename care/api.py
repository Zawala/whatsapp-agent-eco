"""HTTP API in front of the assistant, called by the WhatsApp integration and the agent desk.

    CARE_API_TOKEN=... CARE_DATABASE_URL=postgresql://... uvicorn care.api:create_app --factory

POST /messages handles one inbound message:
  - a message ID seen before gets the stored outcome back (duplicate=true)
  - the message joins the customer's open conversation, or opens a new one if
    there's none or it's been idle past the window
  - if an agent owns the conversation, the bot doesn't reply (reply=null)
  - otherwise the assistant answers; any handoff gives the conversation to an agent
Agent desk: /conversations/{msisdn}/pickup, /release (back to the bot) and /close.

msisdn and area are trusted: they must come from the WhatsApp layer, never from
message text. Every route except /health needs `Authorization: Bearer <token>`.
Several workers can share one database; see care/state.py for how a customer's
messages are kept in order. CARE_CONVERSATION_WINDOW_HOURS sets the window (24).
"""
import hmac
import os
import time
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import llm as llm_module
from .assistant import handle
from .state import AGENT, Conflict, NotFound, Outcome, Store, store_from_env
from .tools import Session

MSISDN = r"^\d{9,15}$"
Msisdn = Annotated[str, Path(pattern=MSISDN)]


class InboundMessage(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)
    msisdn: str = Field(pattern=MSISDN)
    area: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=4096)   # WhatsApp's own limit for a text message


class MessageResult(BaseModel):
    message_id: str
    conversation_id: str
    status: Literal["bot", "agent", "closed"]
    reply: str | None
    route: str
    handoff: bool
    blocked: list[str]
    duplicate: bool


class Pickup(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)


class Close(BaseModel):
    reason: Literal["resolved", "agent_resolved"]


def create_app(token: str | None = None, store: Store | None = None, llm=None) -> FastAPI:
    token = token or os.environ.get("CARE_API_TOKEN")
    if not token:
        raise RuntimeError("set CARE_API_TOKEN")
    store = store or store_from_env()
    llm = llm or llm_module.chat

    def auth(authorization: str = Header(default="")) -> None:
        if not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "bad token")

    app = FastAPI(title="care-bot")

    @app.exception_handler(NotFound)
    def not_found(_, e: NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(Conflict)
    def conflict(_, e: Conflict) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/messages", dependencies=[Depends(auth)])
    def post_message(msg: InboundMessage) -> MessageResult:
        with store.turn(msg.msisdn) as turn:
            if seen := turn.claim(msg.message_id):
                return _result(seen, turn.status_of(seen.conversation_id), duplicate=True)
            conv = turn.conversation
            if conv.status == AGENT:
                outcome = Outcome(msg.message_id, msg.msisdn, conv.conversation_id, "agent", False, [], None)
            else:
                start = time.perf_counter()
                r = handle(msg.text, Session(msisdn=msg.msisdn, area=msg.area, message_id=msg.message_id), llm)
                outcome = Outcome(msg.message_id, msg.msisdn, conv.conversation_id, r.route, r.handoff, r.blocked,
                                  r.text, r.intent, r.confidence, round((time.perf_counter() - start) * 1000))
                if r.handoff:
                    turn.hand_off(",".join([r.route, *r.blocked]))
            turn.finish(outcome)
            return _result(outcome, turn.conversation.status, duplicate=False)

    @app.get("/conversations/{msisdn}", dependencies=[Depends(auth)])
    def get_conversation(msisdn: Msisdn) -> dict:
        """The open conversation (or the latest one) with its messages and events."""
        conv = store.conversation(msisdn)
        if conv is None:
            raise HTTPException(404, f"no conversations for {msisdn}")
        return {**asdict(conv),
                "messages": [asdict(o) for o in store.messages(conv.conversation_id)],
                "events": [asdict(e) for e in store.events(conv.conversation_id)]}

    @app.get("/customers/{msisdn}/conversations", dependencies=[Depends(auth)])
    def list_conversations(msisdn: Msisdn) -> list[dict]:
        return [asdict(c) for c in store.conversations(msisdn)]

    @app.post("/conversations/{msisdn}/pickup", dependencies=[Depends(auth)])
    def pickup(msisdn: Msisdn, body: Pickup) -> dict:
        return asdict(store.pickup(msisdn, body.agent_id))

    @app.post("/conversations/{msisdn}/release", dependencies=[Depends(auth)])
    def release(msisdn: Msisdn) -> dict:
        return asdict(store.release(msisdn))

    @app.post("/conversations/{msisdn}/close", dependencies=[Depends(auth)])
    def close(msisdn: Msisdn, body: Close) -> dict:
        return asdict(store.close_conversation(msisdn, body.reason))

    return app


def _result(o: Outcome, status: str, duplicate: bool) -> MessageResult:
    return MessageResult(message_id=o.message_id, conversation_id=str(o.conversation_id), status=status,
                         reply=o.reply, route=o.route, handoff=o.handoff, blocked=o.blocked, duplicate=duplicate)
