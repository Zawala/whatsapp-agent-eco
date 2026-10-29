# 2. Follow a message through the code

**Goal:** know which function does what, by following two messages from start
to finish.
**You need:** the code open next to this page. No commands to run until the
exercises.

Keep the [pipeline diagram](../reference/architecture.md#2-message-pipeline)
open as a map.

## Message A: "How much is the 5GB bundle?"

This is the longest path, and the only one with two model calls.

1. **[`assistant.handle`](../../care/assistant.py)** is the single entry point.
   It opens a trace span and calls `_pipeline`. If anything raises, it catches
   it and returns a handoff with `route="error"`. That's why the customer never
   sees an error.
2. **`_pipeline` → [`guard.redact`](../../care/guard.py)** with
   `INPUT_ENTITIES` replaces phone numbers, IDs, emails and names with
   placeholders such as `<ZW_PHONE>`. Everything after this, including the
   model, sees only redacted text.
3. **[`router.is_high_risk`](../../care/router.py)** is a regex check. If it
   matches, the pipeline returns a handoff immediately, with no model call.
4. **`router.classify`** sends the `SYSTEM` prompt and the message (wrapped in
   `<<<` `>>>`) to the model in JSON mode. **`router.validate`** checks the
   reply exactly: allowed intent, allowed language, a confidence between 0 and
   1. Bad output gets one retry, with the error sent back to the model. After
   that, `FALLBACK` (`unknown`) applies.
5. **`assistant._dispatch`** applies the confidence gate (`CONF_MIN = 0.6`)
   and the `other`/`unknown` handoff, then picks a handler by intent:
   `bundle_info` → `_bundle`.
6. **`_bundle` → [`retrieval.retrieve`](../../care/retrieval.py)** scores the
   [catalogue](../../care/knowledge.py) entries that are currently in effect by
   keyword overlap, and keeps only those within 80% of the best score. Here
   that's just `data-5gb`.
7. **The second model call** writes the answer from that context only. The
   `SYSTEM` prompt in `assistant.py` contains a canary string.
8. **[`checks.run_all`](../../care/checks.py)** checks the answer:
   - every price must be in the retrieved docs;
   - no banned phrases ("refund", "guarantee"…);
   - no canary.

   Any violation means a handoff.
9. **Back in `handle`**, the reply is redacted again with `OUTPUT_ENTITIES`,
   and the decision attributes go on the span.

## Message B: "My calls keep dropping"

Steps 1–5 are the same. Then:

6. **`_dispatch` → `_fault`** calls
   **[`tools.call_tool`](../../care/tools.py)**`("create_ticket", {category,
   summary}, session)`. The `summary` is a fixed label, so the customer's
   words never reach a backend.
7. **`call_tool`** checks, in order:
   - is the tool on the allowlist (`TOOLS`)?
   - are the arguments only ones the tool declares in `model_args`?
   - does it need a human approver?

   Then it runs the fake backend. Errors come back as `{"ok": false, "error":
   …}`, never as exceptions.
8. **`_create_ticket`** keys the ticket on `session.message_id`, so the same
   message twice gives the same ticket.
9. **`_fault`** fills a fixed template with the ticket ID. The model never
   writes this reply.

Notice where identity comes from: `session.msisdn` is set by the channel (the
API request), never by the model or the message text. No tool declares
`msisdn` in `model_args`, so the model has no way to pass one. See
[architecture §6](../reference/architecture.md#6-tool-gate).

## Over HTTP: what the API adds

[`api.post_message`](../../care/api.py) wraps `handle` in
**[`Store.turn`](../../care/state.py)**, which is one Postgres transaction.
It:

- locks the customer;
- claims the `message_id` (or returns the stored result for a redelivery);
- finds or opens the conversation;
- skips the bot entirely if an agent owns the conversation;
- records the outcome.

[Guide 4](04-conversations.md) covers this in detail.

## Exercises

Run these in `python` from the repo root:

```python
from care import router, retrieval, checks
router.is_high_risk("Can I change my bundle?")                         # False: why doesn't "change" match?
[d["id"] for d in retrieval.retrieve("What data bundles do you have?")]  # ['data-1gb', 'data-5gb', 'data-20gb']
checks.run_all("The 5GB bundle costs $4.00.", {4.50})                   # ['price_not_in_context:4.0']
```

1. Find the regex alternative in `HIGH_RISK` that would catch "change my SIM",
   and explain why "change my bundle" doesn't match it.
2. Why does the second `retrieve` call return three bundles but the 5GB
   question returns one? Look at `score` and `ratio`.
3. In `test_core.py`, find the test that proves a high-risk message makes zero
   model calls. How does `FakeLLM` make that possible to check?

**Next:** [try to break the guardrails](03-guardrails.md).
