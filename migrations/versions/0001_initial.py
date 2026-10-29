"""Conversations and message outcomes.

Revision ID: 0001
"""
from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.execute("""
        CREATE TABLE conversations (
            msisdn     text PRIMARY KEY,
            status     text NOT NULL CHECK (status IN ('bot', 'agent')),
            reason     text,
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE messages (
            message_id  text PRIMARY KEY,
            msisdn      text NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            route       text,
            handoff     boolean,
            blocked     jsonb,
            reply       text
        );
        CREATE INDEX messages_msisdn ON messages (msisdn, received_at);
    """)


def downgrade():
    op.execute("DROP TABLE messages; DROP TABLE conversations;")
