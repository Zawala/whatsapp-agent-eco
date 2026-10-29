# 0003: Postgres, not Mongo, for conversation state

**Status:** accepted. Replaced the single-process SQLite store used for the
first version of the API.

## Context

Conversation state has to be shared by several API workers. It must keep one
customer's messages in order, never process a message twice, and support churn
analysis across customers, conversations and events. Both Postgres and MongoDB
were considered.

## Decision

Postgres, with the schema in Alembic migrations.

1. **The churn questions are joins and time windows.** "Same customer, same
   intent, within 7 days", time to pickup, contacts per month: in SQL these are
   a few lines and can be views (see the
   [handy queries](../reference/data-model.md#handy-queries)). In Mongo they'd
   be `$lookup`/`$group` pipelines.
2. **The core guarantees are simpler.**
   - *One customer at a time:* Postgres has advisory locks
     ([0004](0004-per-customer-advisory-lock.md)). Mongo has nothing
     equivalent, so it would need a lease document per customer, with expiry
     and handling for a dead worker.
   - *Claim a message once:* the same in both (a unique key plus an insert).
   - *Update the conversation, message and event together:* ordinary Postgres
     transactions. Mongo's multi-document transactions need a replica set,
     even in development.
3. **Small step from what existed:** the store was already SQL, and
   SQLAlchemy and Alembic were already dependencies.

## Consequences

- The flexible part (structured context, phase 3) goes in `jsonb`.
- One Postgres handles this load easily: a million messages a day is about 12
  writes a second.
- Postgres is now a dependency of the API and of `test_api.py`
  ([run the tests](../how-to/run-tests.md)).
- **When Mongo would have been right:** if the organisation already runs Mongo
  in production, with backups, monitoring and on-call staff who know it.
  Operations experience outweighs the points above. If that changes, the plan
  is: lease documents instead of advisory locks, a unique index on
  `message_id`, separate collections (no embedding), and churn queries in the
  warehouse.
