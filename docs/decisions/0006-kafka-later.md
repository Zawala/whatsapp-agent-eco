# 0006: Kafka moves messages later; it doesn't store state

**Status:** accepted; Kafka is not used yet.

## Context

The question was whether Kafka could hold conversation state.

## Decision

**No.** Kafka is a log. You can't ask it "who owns this customer's
conversation right now?" in the middle of a request without rebuilding that
answer from the log, which means running Kafka Streams (Java) or a consumer
that copies everything into a database anyway. Postgres stays the source of
truth ([0003](0003-postgres-over-mongo.md)).

When it's needed, Kafka is for **moving messages**:

- **an inbound topic keyed by msisdn:** one customer's messages land in one
  partition, so they're processed in order, one at a time. That replaces the
  request-time lock ([0004](0004-per-customer-advisory-lock.md)) and its
  held connection. It also absorbs bursts, so slow model calls don't block
  the WhatsApp webhook;
- **outbound and handoff topics** that the sender and the agent desk consume,
  so each side can go down and catch up later;
- **an audit and analytics stream** of outcomes, fed from the `events` table
  through a transactional outbox.

## Consequences

- **Still needed:** Kafka delivers at least once, so the `message_id` claim in
  Postgres still prevents double processing.
- **When to add it:** with several workers or nodes, or when the webhook needs
  to respond before the model finishes. A consumer would call the same
  `handle()` and `Store.turn`. The schema doesn't change.
- **Locally:** Redpanda (a single binary that speaks the Kafka protocol) for
  development.

This is phase 5 on the [roadmap](../roadmap.md).
