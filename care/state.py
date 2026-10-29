"""Conversation state in Postgres: customers, conversations, message outcomes and events.

A customer (one msisdn) has many conversations over time and at most one open.
An open conversation is "bot" (the assistant replies) or "agent" (a person has
it; the assistant stays quiet until it's released). A conversation continues
while the customer keeps writing within `window` (default 24 h, WhatsApp's
customer-service window); after that it's closed and the next message opens a
new one. Close reasons:
  timeout         idle past the window
  abandoned       idle past the window after a handoff nobody picked up
  resolved        closed explicitly while the bot owned it
  agent_resolved  closed by an agent
Every change of state is also written to `events`, the source for churn analysis.

Each inbound message is handled in one transaction (`Store.turn`) that:
  - holds a Postgres advisory lock on the msisdn, so one customer's messages run
    one at a time across every worker, while different customers run in parallel
  - claims the message ID by inserting it first; if the row already exists the
    message is a redelivery and the stored outcome is returned instead
Every other change to a customer's conversation takes the same lock.

If the worker dies mid-message the transaction rolls back, the claim goes with
it, and WhatsApp's redelivery is handled from scratch. The lock is held through
the model call, so each in-flight message uses one pooled connection.

Customer text is not stored, only decisions and the (already redacted) reply.
Schema lives in migrations/ (`alembic upgrade head`).
"""
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

BOT, AGENT, CLOSED = "bot", "agent", "closed"
EXPLICIT_CLOSE = {"resolved", "agent_resolved"}

# Same key space for every caller of the lock; hashtextextended gives a bigint.
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"


class NotFound(LookupError):
    """The customer has no open conversation."""


class Conflict(ValueError):
    """The conversation isn't in a state that allows this."""


@dataclass
class Conversation:
    conversation_id: UUID
    msisdn: str
    status: str
    opened_at: datetime
    last_activity_at: datetime
    handoff_at: datetime | None
    handoff_reason: str | None
    agent_id: str | None
    picked_up_at: datetime | None
    closed_at: datetime | None
    close_reason: str | None


@dataclass
class Outcome:
    message_id: str
    msisdn: str
    conversation_id: UUID | None
    route: str
    handoff: bool
    blocked: list[str]
    reply: str | None      # None when an agent owns the conversation
    intent: str | None = None
    confidence: float | None = None
    latency_ms: int | None = None


@dataclass
class Event:
    type: str
    at: datetime
    payload: dict


_CONVERSATION = ("SELECT c.conversation_id, cu.msisdn, c.status, c.opened_at, c.last_activity_at, c.handoff_at, "
                 "c.handoff_reason, c.agent_id, c.picked_up_at, c.closed_at, c.close_reason "
                 "FROM conversations c JOIN customers cu USING (customer_id)")
_OUTCOME = ("SELECT message_id, msisdn, conversation_id, route, handoff, blocked, reply, intent, confidence, "
            "latency_ms FROM messages")


class Turn:
    """One customer's message, inside the transaction that holds their lock."""

    def __init__(self, conn, msisdn: str, window: timedelta):
        self._conn, self.msisdn, self._window = conn, msisdn, window
        self._conversation: Conversation | None = None

    def claim(self, message_id: str) -> Outcome | None:
        """Claim the message. Returns None if it's new, or the stored outcome if it was handled before."""
        row = self._conn.execute("INSERT INTO messages (message_id, msisdn) VALUES (%s, %s) "
                                 "ON CONFLICT (message_id) DO NOTHING RETURNING message_id",
                                 (message_id, self.msisdn)).fetchone()
        if row:
            return None
        return _one(self._conn, Outcome, f"{_OUTCOME} WHERE message_id = %s", message_id)

    @property
    def conversation(self) -> Conversation:
        """The customer's open conversation, opened (or rolled over after the window) on first use."""
        if self._conversation is None:
            self._conversation = _current(self._conn, self.msisdn, self._window, open_new=True)
        return self._conversation

    def status_of(self, conversation_id: UUID | None) -> str:
        """Status of a given conversation, for replaying a stored outcome."""
        conv = _one(self._conn, Conversation, f"{_CONVERSATION} WHERE c.conversation_id = %s", conversation_id)
        return conv.status if conv else BOT

    def hand_off(self, reason: str) -> None:
        conv = self.conversation
        # A new handoff needs a new pickup, even if an earlier one was picked up.
        self._conn.execute("UPDATE conversations SET status = %s, handoff_at = now(), handoff_reason = %s, "
                           "agent_id = NULL, picked_up_at = NULL WHERE conversation_id = %s",
                           (AGENT, reason, conv.conversation_id))
        _event(self._conn, conv.conversation_id, "handoff", {"reason": reason})
        self._conversation = _by_id(self._conn, conv.conversation_id)

    def finish(self, o: Outcome) -> None:
        self._conn.execute("UPDATE messages SET conversation_id = %s, route = %s, handoff = %s, blocked = %s, "
                           "reply = %s, intent = %s, confidence = %s, latency_ms = %s WHERE message_id = %s",
                           (o.conversation_id, o.route, o.handoff, Jsonb(o.blocked), o.reply, o.intent,
                            o.confidence, o.latency_ms, o.message_id))
        self._conn.execute("UPDATE conversations SET last_activity_at = now() WHERE conversation_id = %s",
                           (o.conversation_id,))


class Store:
    def __init__(self, url: str, max_size: int = 10, window: timedelta = timedelta(hours=24)):
        # max_size caps how many messages can be in flight at once (one connection each).
        self._pool = ConnectionPool(url, min_size=1, max_size=max_size, open=True)
        self.window = window

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _locked(self, msisdn: str):
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(_LOCK, (msisdn,))
            yield conn

    @contextmanager
    def turn(self, msisdn: str) -> Iterator[Turn]:
        with self._locked(msisdn) as conn:
            yield Turn(conn, msisdn, self.window)

    # --- agent actions --------------------------------------------------------
    def pickup(self, msisdn: str, agent_id: str) -> Conversation:
        with self._locked(msisdn) as conn:
            conv = _open_or_raise(conn, msisdn, self.window)
            if conv.status != AGENT:
                raise Conflict(f"conversation is {conv.status}, not waiting for an agent")
            conn.execute("UPDATE conversations SET agent_id = %s, picked_up_at = now() WHERE conversation_id = %s",
                         (agent_id, conv.conversation_id))
            _event(conn, conv.conversation_id, "picked_up", {"agent_id": agent_id})
            return _by_id(conn, conv.conversation_id)

    def release(self, msisdn: str) -> Conversation:
        """Give the conversation back to the bot. Waits for any message in progress."""
        with self._locked(msisdn) as conn:
            conv = _open_or_raise(conn, msisdn, self.window)
            if conv.status != AGENT:
                raise Conflict(f"conversation is {conv.status}, there's nothing to release")
            conn.execute("UPDATE conversations SET status = %s WHERE conversation_id = %s",
                         (BOT, conv.conversation_id))
            _event(conn, conv.conversation_id, "released", {})
            return _by_id(conn, conv.conversation_id)

    def close_conversation(self, msisdn: str, reason: str) -> Conversation:
        if reason not in EXPLICIT_CLOSE:
            raise ValueError(f"reason must be one of {sorted(EXPLICIT_CLOSE)}")
        with self._locked(msisdn) as conn:
            conv = _open_or_raise(conn, msisdn, self.window)
            _close(conn, conv.conversation_id, reason)
            return _by_id(conn, conv.conversation_id)

    # --- sweeper ----------------------------------------------------------------
    def close_idle(self) -> int:
        """Close every open conversation idle past the window. Returns how many were closed.

        Only keeps metrics fresh: a turn closes a stale conversation itself."""
        with self._pool.connection() as conn:
            stale = [r[0] for r in conn.execute(
                "SELECT cu.msisdn FROM conversations c JOIN customers cu USING (customer_id) "
                "WHERE c.status <> 'closed' AND c.last_activity_at < now() - %s", (self.window,))]
        closed = 0
        for msisdn in stale:
            with self._locked(msisdn) as conn:      # re-check under the lock: a message may just have arrived
                if _open(conn, msisdn) and _current(conn, msisdn, self.window, open_new=False) is None:
                    closed += 1
        return closed

    # --- reads ------------------------------------------------------------------
    def conversation(self, msisdn: str) -> Conversation | None:
        """The open conversation, or the most recent one, or None for a new customer."""
        with self._pool.connection() as conn:
            return _open(conn, msisdn) or _one(conn, Conversation, f"{_CONVERSATION} WHERE cu.msisdn = %s "
                                               "ORDER BY c.opened_at DESC LIMIT 1", msisdn)

    def conversations(self, msisdn: str, limit: int = 20) -> list[Conversation]:
        with self._pool.connection() as conn, conn.cursor(row_factory=class_row(Conversation)) as cur:
            return cur.execute(f"{_CONVERSATION} WHERE cu.msisdn = %s ORDER BY c.opened_at DESC LIMIT %s",
                               (msisdn, limit)).fetchall()

    def messages(self, conversation_id: UUID, limit: int = 50) -> list[Outcome]:
        with self._pool.connection() as conn, conn.cursor(row_factory=class_row(Outcome)) as cur:
            return cur.execute(f"{_OUTCOME} WHERE conversation_id = %s AND route IS NOT NULL "
                               "ORDER BY received_at DESC LIMIT %s", (conversation_id, limit)).fetchall()

    def events(self, conversation_id: UUID) -> list[Event]:
        with self._pool.connection() as conn, conn.cursor(row_factory=class_row(Event)) as cur:
            return cur.execute("SELECT type, at, payload FROM events WHERE conversation_id = %s "
                               "ORDER BY event_id", (conversation_id,)).fetchall()


def store_from_env() -> Store:
    """Store from CARE_DATABASE_URL and CARE_CONVERSATION_WINDOW_HOURS (default 24)."""
    if not os.environ.get("CARE_DATABASE_URL"):
        raise RuntimeError("set CARE_DATABASE_URL")
    hours = float(os.environ.get("CARE_CONVERSATION_WINDOW_HOURS", "24"))
    return Store(os.environ["CARE_DATABASE_URL"], window=timedelta(hours=hours))


# --- helpers; every caller already holds the customer's lock where it writes ---
def _one(conn, cls, sql: str, *params):
    with conn.cursor(row_factory=class_row(cls)) as cur:
        return cur.execute(sql, params).fetchone()


def _by_id(conn, conversation_id: UUID) -> Conversation:
    return _one(conn, Conversation, f"{_CONVERSATION} WHERE c.conversation_id = %s", conversation_id)


def _open(conn, msisdn: str) -> Conversation | None:
    return _one(conn, Conversation, f"{_CONVERSATION} WHERE cu.msisdn = %s AND c.status <> 'closed'", msisdn)


def _current(conn, msisdn: str, window: timedelta, open_new: bool) -> Conversation | None:
    """Apply the lifecycle: close the open conversation if it's idle past the window,
    then (if open_new, i.e. for a customer's message) make sure they have an open one."""
    if open_new:
        customer_id = conn.execute("INSERT INTO customers (msisdn) VALUES (%s) ON CONFLICT (msisdn) "
                                   "DO UPDATE SET last_seen = now() RETURNING customer_id", (msisdn,)).fetchone()[0]
    conv = _open(conn, msisdn)
    if conv:
        idle = conn.execute("SELECT %s < now() - %s", (conv.last_activity_at, window)).fetchone()[0]
        if idle:
            unpicked = conv.status == AGENT and conv.picked_up_at is None
            _close(conn, conv.conversation_id, "abandoned" if unpicked else "timeout")
            conv = None
    if conv or not open_new:
        return conv
    conversation_id = conn.execute("INSERT INTO conversations (customer_id) VALUES (%s) RETURNING conversation_id",
                                   (customer_id,)).fetchone()[0]
    _event(conn, conversation_id, "opened", {})
    return _by_id(conn, conversation_id)


def _open_or_raise(conn, msisdn: str, window: timedelta) -> Conversation:
    conv = _current(conn, msisdn, window, open_new=False)
    if conv is None:
        raise NotFound(f"no open conversation for {msisdn}")
    return conv


def _close(conn, conversation_id: UUID, reason: str) -> None:
    conn.execute("UPDATE conversations SET status = %s, closed_at = now(), close_reason = %s "
                 "WHERE conversation_id = %s", (CLOSED, reason, conversation_id))
    _event(conn, conversation_id, "closed", {"reason": reason})


def _event(conn, conversation_id: UUID, type_: str, payload: dict) -> None:
    conn.execute("INSERT INTO events (conversation_id, customer_id, type, payload) "
                 "SELECT conversation_id, customer_id, %s, %s FROM conversations WHERE conversation_id = %s",
                 (type_, Jsonb(payload), conversation_id))
