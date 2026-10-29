# 0004: One advisory lock per customer, held through the model call

**Status:** accepted

## Context

Several API workers handle messages at once. Without coordination, two
messages from the same customer could both read "the bot owns this
conversation", both run, and both write. Then a handoff could be overwritten,
or the bot could reply while an agent is talking. WhatsApp also redelivers
messages, sometimes while the first copy is still being handled.

## Decision

Each message is one transaction (`Store.turn`) that:

1. takes `pg_advisory_xact_lock(hashtextextended(msisdn, 0))`;
2. claims the `message_id` by inserting it first (`ON CONFLICT DO NOTHING`);
3. opens or continues the conversation, runs the bot, and writes the outcome;
4. commits, which releases the lock.

Agent actions (`pickup`, `release`, `close`) and the sweeper take the same lock.

## Consequences

- **Ordering:** one customer's messages are strictly in order across all
  workers, and different customers never wait on each other.
- **Crash safety:** if a worker dies mid-message, everything rolls back,
  including the claim, so the redelivery is processed normally.
- **Cost:** the lock and a database connection are held for the whole model
  call (10–30 s on CPU). Each in-flight message uses one pooled connection, so
  `workers × Store max_size` (default 10) must stay under Postgres's
  `max_connections`.
- **Escape hatch:** a queue partitioned by msisdn
  ([0006](0006-kafka-later.md)) gives the same per-customer ordering without
  holding a connection. The schema doesn't change.
- **Tested** in `test_api.py`: one customer one message at a time, customers in
  parallel, a raced message processed once, release waiting for a turn, a crash
  releasing the claim, the sweeper not racing a turn.

See [data model → concurrency](../reference/data-model.md#concurrency).
