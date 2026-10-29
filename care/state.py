"""Conversation state in Postgres: who owns each conversation, and which messages are handled.

A conversation is keyed by msisdn and is either "bot" (the assistant replies) or
"agent" (a person has it; the assistant stays quiet until it's released).
Every handoff moves it to "agent".

Each inbound message is handled in one transaction (`Store.turn`) that:
  - holds a Postgres advisory lock on the msisdn, so one customer's messages run
    one at a time across every worker, while different customers run in parallel
  - claims the message ID by inserting it first; if the row already exists the
    message is a redelivery and the stored outcome is returned instead

If the worker dies mid-message the transaction rolls back, the claim goes with
it, and WhatsApp's redelivery is handled from scratch. The lock is held through
the model call, so each in-flight message uses one pooled connection.

Customer text is not stored, only decisions and the (already redacted) reply.
Schema lives in migrations/ (`alembic upgrade head`).
"""
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

BOT, AGENT = "bot", "agent"

# Same key space for every caller of the lock; hashtextextended gives a bigint.
_LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"


@dataclass
class Conversation:
    msisdn: str
    status: str
    reason: str | None
    updated_at: datetime | None


@dataclass
class Outcome:
    message_id: str
    msisdn: str
    route: str
    handoff: bool
    blocked: list[str]
    reply: str | None      # None when an agent owns the conversation


_OUTCOME = "SELECT message_id, msisdn, route, handoff, blocked, reply FROM messages"


class Turn:
    """One customer's message, inside the transaction that holds their lock."""

    def __init__(self, conn, msisdn: str):
        self._conn, self.msisdn = conn, msisdn

    def claim(self, message_id: str) -> Outcome | None:
        """Claim the message. Returns None if it's new, or the stored outcome if it was handled before."""
        row = self._conn.execute("INSERT INTO messages (message_id, msisdn) VALUES (%s, %s) "
                                 "ON CONFLICT (message_id) DO NOTHING RETURNING message_id",
                                 (message_id, self.msisdn)).fetchone()
        if row:
            return None
        with self._conn.cursor(row_factory=class_row(Outcome)) as cur:
            return cur.execute(f"{_OUTCOME} WHERE message_id = %s", (message_id,)).fetchone()

    def status(self) -> str:
        return _conversation(self._conn, self.msisdn).status

    def set_status(self, status: str, reason: str | None = None) -> None:
        _set_status(self._conn, self.msisdn, status, reason)

    def finish(self, o: Outcome) -> None:
        self._conn.execute("UPDATE messages SET route = %s, handoff = %s, blocked = %s, reply = %s "
                           "WHERE message_id = %s", (o.route, o.handoff, Jsonb(o.blocked), o.reply, o.message_id))


class Store:
    def __init__(self, url: str, max_size: int = 10):
        # max_size caps how many messages can be in flight at once (one connection each).
        self._pool = ConnectionPool(url, min_size=1, max_size=max_size, open=True)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def turn(self, msisdn: str) -> Iterator[Turn]:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(_LOCK, (msisdn,))
            yield Turn(conn, msisdn)

    def conversation(self, msisdn: str) -> Conversation:
        with self._pool.connection() as conn:
            return _conversation(conn, msisdn)

    def release(self, msisdn: str) -> Conversation:
        """Give the conversation back to the bot. Waits for any message in progress."""
        with self.turn(msisdn) as t:
            t.set_status(BOT, "released")
            return _conversation(t._conn, msisdn)

    def history(self, msisdn: str, limit: int = 20) -> list[Outcome]:
        with self._pool.connection() as conn, conn.cursor(row_factory=class_row(Outcome)) as cur:
            return cur.execute(f"{_OUTCOME} WHERE msisdn = %s AND route IS NOT NULL "
                               "ORDER BY received_at DESC LIMIT %s", (msisdn, limit)).fetchall()


def _conversation(conn, msisdn: str) -> Conversation:
    with conn.cursor(row_factory=class_row(Conversation)) as cur:
        row = cur.execute("SELECT msisdn, status, reason, updated_at FROM conversations WHERE msisdn = %s",
                          (msisdn,)).fetchone()
    return row or Conversation(msisdn, BOT, None, None)


def _set_status(conn, msisdn: str, status: str, reason: str | None) -> None:
    conn.execute("INSERT INTO conversations (msisdn, status, reason) VALUES (%s, %s, %s) "
                 "ON CONFLICT (msisdn) DO UPDATE SET status = excluded.status, reason = excluded.reason, "
                 "updated_at = now()", (msisdn, status, reason))
