"""Customers, conversations that open and close, and an events log.

Revision ID: 0002
Revises: 0001

Each msisdn from 0001 becomes a customer with one open conversation that keeps
its status, and its messages are linked to that conversation.
"""
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    op.execute("""
        CREATE TABLE customers (
            customer_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            msisdn      text NOT NULL UNIQUE,
            first_seen  timestamptz NOT NULL DEFAULT now(),
            last_seen   timestamptz NOT NULL DEFAULT now()
        );

        ALTER TABLE conversations RENAME TO conversations_0001;

        CREATE TABLE conversations (
            conversation_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id      uuid NOT NULL REFERENCES customers,
            status           text NOT NULL DEFAULT 'bot' CHECK (status IN ('bot', 'agent', 'closed')),
            opened_at        timestamptz NOT NULL DEFAULT now(),
            last_activity_at timestamptz NOT NULL DEFAULT now(),
            handoff_at       timestamptz,
            handoff_reason   text,
            agent_id         text,
            picked_up_at     timestamptz,
            closed_at        timestamptz,
            close_reason     text CHECK (close_reason IN ('timeout', 'abandoned', 'resolved', 'agent_resolved')),
            CHECK ((status = 'closed') = (closed_at IS NOT NULL AND close_reason IS NOT NULL))
        );
        -- At most one open conversation per customer.
        CREATE UNIQUE INDEX conversations_one_open ON conversations (customer_id) WHERE status <> 'closed';
        CREATE INDEX conversations_customer ON conversations (customer_id, opened_at);

        CREATE TABLE events (
            event_id        bigserial PRIMARY KEY,
            conversation_id uuid NOT NULL REFERENCES conversations,
            customer_id     uuid NOT NULL REFERENCES customers,
            type            text NOT NULL CHECK (type IN ('opened', 'handoff', 'picked_up', 'released', 'closed')),
            at              timestamptz NOT NULL DEFAULT now(),
            payload         jsonb NOT NULL DEFAULT '{}'
        );
        CREATE INDEX events_customer ON events (customer_id, at);
        CREATE INDEX events_type ON events (type, at);

        ALTER TABLE messages
            ADD COLUMN conversation_id uuid REFERENCES conversations,
            ADD COLUMN intent          text,
            ADD COLUMN confidence      real,
            ADD COLUMN latency_ms      integer;
        CREATE INDEX messages_conversation ON messages (conversation_id, received_at);

        -- Backfill from 0001.
        INSERT INTO customers (msisdn, first_seen, last_seen)
        SELECT msisdn, min(at), max(at) FROM (
            SELECT msisdn, received_at AS at FROM messages
            UNION ALL SELECT msisdn, updated_at FROM conversations_0001
        ) seen GROUP BY msisdn;

        INSERT INTO conversations (customer_id, status, opened_at, last_activity_at, handoff_at, handoff_reason)
        SELECT cu.customer_id, coalesce(old.status, 'bot'), cu.first_seen, cu.last_seen,
               CASE WHEN old.status = 'agent' THEN old.updated_at END,
               CASE WHEN old.status = 'agent' THEN old.reason END
        FROM customers cu LEFT JOIN conversations_0001 old USING (msisdn);

        INSERT INTO events (conversation_id, customer_id, type, at, payload)
        SELECT conversation_id, customer_id, 'opened', opened_at, '{"backfilled": true}' FROM conversations;

        UPDATE messages m SET conversation_id = c.conversation_id
        FROM conversations c JOIN customers cu USING (customer_id)
        WHERE cu.msisdn = m.msisdn;

        DROP TABLE conversations_0001;
    """)


def downgrade():
    # Keeps each customer's open conversation as their 0001 row; closed history is lost.
    op.execute("""
        CREATE TABLE conversations_0001 (
            msisdn     text PRIMARY KEY,
            status     text NOT NULL CHECK (status IN ('bot', 'agent')),
            reason     text,
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        INSERT INTO conversations_0001 (msisdn, status, reason, updated_at)
        SELECT cu.msisdn, c.status, CASE WHEN c.status = 'agent' THEN c.handoff_reason END, c.last_activity_at
        FROM conversations c JOIN customers cu USING (customer_id)
        WHERE c.status <> 'closed';

        ALTER TABLE messages
            DROP COLUMN conversation_id, DROP COLUMN intent, DROP COLUMN confidence, DROP COLUMN latency_ms;
        DROP TABLE events;
        DROP TABLE conversations;
        DROP TABLE customers;
        ALTER TABLE conversations_0001 RENAME TO conversations;
    """)
