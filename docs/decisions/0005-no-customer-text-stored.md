# 0005: Never store what the customer wrote

**Status:** accepted for now. Storing redacted text is an
[open decision](../roadmap.md#open-decisions).

## Context

Customer messages contain personal data: names, numbers, account problems.
Storage that holds it falls under Zimbabwe's Cyber and Data Protection Act, and
needs retention rules, access control and breach handling. Traces and logs are
the usual way it leaks.

## Decision

Store **decisions, not messages**:

- **`messages`** rows hold the route, intent, confidence, latency, handoff,
  blocked reasons, and the bot's reply. The reply is already redacted by
  `guard.redact(OUTPUT_ENTITIES)`.
- **`events`** payloads hold reasons and agent IDs only.
- **Traces** record decision attributes; LLM inputs and outputs are hidden
  (`tracing.py`).
- **Ticket summaries** sent to backends are fixed labels, not the customer's
  words.

`test_message_rows_hold_decisions` and
`test_conversation_view_has_no_customer_text` check this.

## Consequences

- **Analytics:** churn analysis works from decisions and events, which is
  enough for repeat contact, abandonment and pickup times.
- **Agents:** they see the conversation in WhatsApp itself, not from this
  store.
- **Follow-ups:** a follow-up like "and the weekly one?" needs context. Phase 3
  plans a structured `context` field (last intent, last bundle IDs) rather than
  the transcript.
- **Debugging:** you can't reread what the customer wrote. You work from
  `route`/`blocked`, the trace, and by reproducing it
  ([debug a reply](../how-to/debug-a-reply.md)). Golden cases must be written
  or redacted by hand.
- **Redaction gaps** matter less here because nothing inbound is stored. They
  still matter for what the model sees. Zimbabwean names needed their own
  recognizers (see [learn: guardrails](../learn/03-guardrails.md#step-5-pii-redaction)).
