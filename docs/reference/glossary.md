# Glossary

| Term | Meaning |
|---|---|
| **abandoned** | Close reason for a conversation that went idle after a handoff nobody picked up. |
| **advisory lock** | A Postgres lock on an arbitrary key, here the msisdn, held for one transaction. Keeps one customer's messages in order across workers. |
| **agent** | A human care agent. Also the conversation status meaning a person owns it and the bot stays quiet. |
| **blocked** | List of reasons a reply was stopped, e.g. `price_not_in_context:4.0`, `tool:not_found`, `no_context`. Empty for a normal answer. |
| **canary** | `CANARY-7f3a`, a string placed only in the bundle-answer system prompt. If it appears in a reply, the prompt leaked. |
| **claim** | Inserting a `message_id` before handling it. A second insert means the message was already handled. |
| **conversation** | A customer's messages while they keep writing within the window. Statuses: `bot`, `agent`, `closed`. |
| **`CONF_MIN`** | 0.6. Below this classifier confidence, the bot hands off. |
| **effective dates** | `effective_from` and `expires` on catalogue entries, so only current prices are served. |
| **event** | An append-only row recording a change to a conversation (`opened`, `handoff`, `picked_up`, `released`, `closed`). |
| **FakeLLM** | Test double that returns scripted model replies and counts calls. |
| **fallback** | `{"intent": "unknown", …}`, used when the classifier fails twice. Always hands off. |
| **golden set** | `evals/golden.jsonl`: labelled messages the classifier is scored against. |
| **handoff** | Passing the customer to a human. The customer sees `HANDOFF_TEXT`; via the API the conversation becomes `agent`. |
| **high-risk** | SIM swap, PIN/PUK/password, reversal, refund, money back, wrong-number payments. Caught by regex before the model. |
| **idempotency key** | `message_id`: the same message always gets the same outcome and ticket. |
| **intent** | What the classifier thinks the customer wants: `bundle_info`, `balance`, `outage`, `fault_report`, `other` (or `unknown` from the fallback). |
| **msisdn** | The customer's phone number in international form, e.g. `263772000001`. Comes from the channel, never from the message text. |
| **Pareto front** | The models no other model beats on both speed and accuracy. |
| **PII** | Personal data: phone numbers, national IDs, emails, names. Redacted on input and output. |
| **redelivery** | WhatsApp sending the same message (same `message_id`) again. |
| **route** | Where a message went: an intent, `high_risk`, `error`, or `agent` (the bot was skipped). |
| **routed accuracy** | The main eval metric: the share of cases with the right intent *and* confidence ≥ 0.6. |
| **Session** | `msisdn`, `area`, `message_id`, `approved_by`: trusted context from the channel, passed to tools. |
| **sweeper** | `care/sweep.py`, run on a timer to close idle conversations. |
| **tool gate** | `call_tool`'s checks: the allowlist, allowed arguments, approval. |
| **turn** | The transaction handling one inbound message (`Store.turn`). |
| **wamid** | WhatsApp message ID, used as `message_id`. |
| **window** | How long a conversation stays open without activity. Default 24 h. |
