"""Conversation state: who owns each conversation, and which messages are already handled.

A conversation is keyed by msisdn and is either "bot" (the assistant replies) or
"agent" (a person has it; the assistant stays quiet until it's released).
Every handoff moves it to "agent".

Each message ID is stored once with its outcome, so a redelivered WhatsApp
message gets the same reply and is never processed twice. Customer text is not
stored, only decisions and the (already redacted) reply.

SQLite, one process. For several workers, move this to Postgres; the interface
stays the same.
"""
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

BOT, AGENT = "bot", "agent"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    msisdn     TEXT PRIMARY KEY,
    status     TEXT NOT NULL CHECK (status IN ('bot', 'agent')),
    reason     TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    message_id  TEXT PRIMARY KEY,
    msisdn      TEXT NOT NULL,
    received_at TEXT NOT NULL,
    route       TEXT NOT NULL,
    handoff     INTEGER NOT NULL,
    blocked     TEXT NOT NULL,
    reply       TEXT
);
CREATE INDEX IF NOT EXISTS messages_msisdn ON messages (msisdn, received_at);
"""


@dataclass
class Conversation:
    msisdn: str
    status: str
    reason: str | None
    updated_at: str


@dataclass
class Outcome:
    message_id: str
    msisdn: str
    route: str
    handoff: bool
    blocked: list[str]
    reply: str | None      # None when an agent owns the conversation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str = "care.db"):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()          # one connection shared by the API's threads
        self._conv_locks: dict[str, threading.Lock] = {}

    def conversation_lock(self, msisdn: str) -> threading.Lock:
        """Held while one message is handled, so a customer's messages run one at a time."""
        with self._lock:
            return self._conv_locks.setdefault(msisdn, threading.Lock())

    def conversation(self, msisdn: str) -> Conversation:
        with self._lock:
            row = self._db.execute("SELECT msisdn, status, reason, updated_at FROM conversations WHERE msisdn = ?",
                                   (msisdn,)).fetchone()
        return Conversation(*row) if row else Conversation(msisdn, BOT, None, _now())

    def set_status(self, msisdn: str, status: str, reason: str | None = None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO conversations (msisdn, status, reason, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (msisdn) DO UPDATE SET status = excluded.status, reason = excluded.reason, "
                "updated_at = excluded.updated_at",
                (msisdn, status, reason, _now()))

    def outcome(self, message_id: str) -> Outcome | None:
        with self._lock:
            row = self._db.execute("SELECT message_id, msisdn, route, handoff, blocked, reply FROM messages "
                                   "WHERE message_id = ?", (message_id,)).fetchone()
        if not row:
            return None
        return Outcome(row[0], row[1], row[2], bool(row[3]), json.loads(row[4]), row[5])

    def record(self, o: Outcome) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (o.message_id, o.msisdn, _now(), o.route, int(o.handoff), json.dumps(o.blocked), o.reply))

    def history(self, msisdn: str, limit: int = 20) -> list[Outcome]:
        with self._lock:
            rows = self._db.execute("SELECT message_id, msisdn, route, handoff, blocked, reply FROM messages "
                                    "WHERE msisdn = ? ORDER BY received_at DESC LIMIT ?", (msisdn, limit)).fetchall()
        return [Outcome(r[0], r[1], r[2], bool(r[3]), json.loads(r[4]), r[5]) for r in rows]
