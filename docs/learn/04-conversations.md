# 4. Conversations and concurrency

**Goal:** understand conversation state by changing it with curl and watching
the tables with SQL.
**You need:**
- a Postgres (see [run the stack](../how-to/run-the-stack.md#postgres)), with
  a fresh database migrated (`alembic upgrade head`);
- the API running:
  `CARE_API_TOKEN=dev uvicorn care.api:create_app --factory --port 8000`.

Set up some shortcuts:

```bash
H='Authorization: Bearer dev'; J='content-type: application/json'; M=263772000001
send() { curl -s -X POST localhost:8000/messages -H "$H" -H "$J" \
  -d "{\"message_id\":\"$1\",\"msisdn\":\"$M\",\"area\":\"harare-cbd\",\"text\":\"$2\"}"; echo; }
Q="psql $CARE_DATABASE_URL"        # or psql -h … -U care -d …
```

Keep the [lifecycle diagram](../reference/data-model.md#lifecycle) open.

## Step 1: a message opens a conversation; a redelivery doesn't

```bash
send wamid.1 "What is my balance?"
send wamid.1 "What is my balance?"
```

Both return the same `conversation_id` and reply. The second has
`"duplicate":true` and came back in milliseconds.

**Why:** [`Store.turn`](../../care/state.py) inserts the `message_id` before
doing anything else (`Turn.claim`). The second insert conflicts, so the
stored outcome is returned instead. A conversation is only opened *after* a
message is known to be new (`Turn.conversation`), so a late redelivery can't
start one by mistake.

## Step 2: a handoff, and the bot going quiet

```bash
send wamid.2 "I want my money back"   # status "agent", route "high_risk"
send wamid.3 "Hello?"                 # "reply":null, "route":"agent"
```

While an agent owns the conversation, messages are recorded but the bot isn't
run at all. In production the WhatsApp integration would forward those
messages to the agent.

## Step 3: the agent picks up and closes

```bash
curl -s -X POST localhost:8000/conversations/$M/pickup -H "$H" -H "$J" -d '{"agent_id":"agent-7"}'
curl -s -X POST localhost:8000/conversations/$M/close  -H "$H" -H "$J" -d '{"reason":"agent_resolved"}'
send wamid.4 "What is my balance?"    # a new conversation_id, status "bot"
curl -s -X POST localhost:8000/conversations/$M/release -H "$H"
# {"detail":"conversation is bot, there's nothing to release"}   (HTTP 409)
```

## Step 4: look at the tables

```bash
$Q -c "SELECT left(conversation_id::text, 8) AS conv, status, close_reason, agent_id FROM conversations ORDER BY opened_at"
```

```
   conv   | status |  close_reason  | agent_id
----------+--------+----------------+----------
 d26e8186 | closed | agent_resolved | agent-7
 72b9d481 | bot    |                |
```

```bash
$Q -c "SELECT left(conversation_id::text, 8) AS conv, type, payload FROM events ORDER BY event_id"
```

```
   conv   |   type    |           payload
----------+-----------+------------------------------
 d26e8186 | opened    | {}
 d26e8186 | handoff   | {"reason": "high_risk"}
 d26e8186 | picked_up | {"agent_id": "agent-7"}
 d26e8186 | closed    | {"reason": "agent_resolved"}
 72b9d481 | opened    | {}
```

```bash
$Q -c "SELECT message_id, route, intent, confidence, latency_ms, handoff FROM messages ORDER BY received_at"
```

```
 message_id |   route   | intent  | confidence | latency_ms | handoff
------------+-----------+---------+------------+------------+---------
 wamid.1    | balance   | balance |       0.95 |      11204 | f
 wamid.2    | high_risk |         |            |         17 | t
 wamid.3    | agent     |         |            |            | f
 wamid.4    | balance   | balance |       0.95 |       9847 | f
```

Notice what's **not** there: what the customer wrote. Only decisions and the
bot's reply are stored ([decision 0005](../decisions/0005-no-customer-text-stored.md)).
`wamid.2` has no intent and took 17 ms, because the high-risk regex stopped it
before the model.

The `events` table is append-only. It's the raw material for churn analysis:
time to pickup is `picked_up.at - handoff.at`, abandonment is `closed` with
reason `abandoned`, and repeat contact is several `opened` events for one
customer within a few days.

## Step 5: the idle window

A conversation closes after 24 h without activity. Rather than waiting, move
time back with SQL:

```bash
send wamid.5 "Please reverse my last transaction"     # handoff; nobody picks it up
$Q -c "UPDATE conversations SET last_activity_at = now() - interval '25 hours' WHERE status <> 'closed'"
send wamid.6 "What is my balance?"                    # new conversation, and the bot answers
$Q -c "SELECT left(conversation_id::text, 8) AS conv, status, close_reason FROM conversations ORDER BY opened_at"
```

```
   conv   | status |  close_reason
----------+--------+----------------
 d26e8186 | closed | agent_resolved
 72b9d481 | closed | abandoned
 d2606edc | bot    |
```

The handed-off conversation that nobody picked up closed as `abandoned`, and
the customer is no longer stuck waiting for an agent.

Customers who never write again are closed by the sweeper:

```bash
$Q -c "UPDATE conversations SET last_activity_at = now() - interval '25 hours' WHERE status <> 'closed'"
python -m care.sweep        # closed 1 idle conversation(s)
```

## Step 6: concurrency

Every message, and every agent action, takes a Postgres **advisory lock on the
msisdn** for the length of its transaction (`Store._locked`). So:

- **One customer's messages run one at a time**, even across several API
  workers. They can't interleave and both read "bot owns it".
- **Different customers never wait on each other.**
- **A release can't land halfway through a message** and then be overwritten
  by that message's handoff.

You can see it: start the API with `--workers 2`, then send two messages from
the same number at once. One reply takes twice as long as the other.

```bash
send wamid.10 "Is the network down?" & send wamid.11 "My calls keep dropping" & wait
```

The tests prove this with fake models that sleep and count overlapping calls.
Read `test_one_customer_is_handled_one_message_at_a_time_across_workers` and
`test_different_customers_run_in_parallel` in
[`test_api.py`](../../tests/test_api.py). The cost of the design is one
database connection per in-flight message; see
[decision 0004](../decisions/0004-per-customer-advisory-lock.md).

## Exercises

1. Hand off a conversation, pick it up, then age it past the window. What
   `close_reason` do you get, and why isn't it `abandoned`? (Look at `_current`
   in `state.py`.)
2. Hand off, release, then hand off again. What happens to `agent_id` and
   `picked_up_at`? Which test checks it?
3. Write the SQL for "median minutes from handoff to pickup" from the `events`
   table.

**Next:** [evals and choosing a model](05-evals.md).
