# Roadmap

Where the project stands against production readiness (the WhatsApp webhook
itself is out of scope), what's planned next, and the decisions still open.
Status as of 2026-10-29.

**Status key:** ✅ done · 🟡 partly done · ⬜ not started

## Production readiness

### Must fix before launch

| # | Item | Status | Notes |
|---|---|---|---|
| 1 | A handoff reaches a person | 🟡 | Conversations move to `agent`, the bot goes quiet, and pickup/release/close endpoints exist. Nothing notifies an agent desk yet, or passes along the context (route, blocked reasons). |
| 2 | Conversation state | ✅ | Postgres store, lifecycle, events, multi-worker safety (phases 1–2). |
| 3 | Real backends | ⬜ | `tools.py` uses in-memory fakes. Ticket idempotency is an in-memory dict, so it's lost on restart; the API's `message_id` claim covers redelivery for now. Remove `reverse_transaction` until a real approval flow exists. |
| 4 | Speed and serving | ⬜ | 12 s per classifier call on CPU, about 30 s for bundle answers. Needs a GPU host or inference server, and one deadline per message (e.g. 15 s, then hand off). |
| 5 | Behaviour when the model is down | ⬜ | Every message hands off silently. Needs an alert, and a fallback reply for routes that don't need the model. |
| 6 | Pin the model version | ⬜ | `mistral-nemo:latest` can change on a pull. Pin the tested digest (`e7e06d107c6c`). |

### Needed to trust it

| # | Item | Status | Notes |
|---|---|---|---|
| 7 | A bigger, multilingual eval set | 🟡 | The CI gate is in place (`evals.yml`, nightly). The set is still 31 English cases; it needs hundreds, from real redacted messages, plus Shona and Ndebele cases written or reviewed by native speakers. |
| 8 | Evaluate bundle answers end to end | ⬜ | Only the classifier is scored. |
| 9 | Language policy | ⬜ | `language` is detected but ignored. Presidio runs in English only, and it misses first names (see the known bugs below). |
| 10 | Better retrieval | ⬜ | Keyword overlap with no stemming: "a month of data" doesn't match "Monthly". |
| 11 | Prices out of code | ⬜ | Changing a price in `knowledge.py` needs a deploy. |

### Running it

| # | Item | Status | Notes |
|---|---|---|---|
| 12 | Abuse limits | 🟡 | Text is capped at 4096 characters. There's no per-number rate limit yet. |
| 13 | Monitoring | 🟡 | Decision traces (Phoenix) and the nightly eval. No production trace backend, metrics or alerts yet (handoff rate, blocked reasons, latency, errors). |
| 14 | Packaging | 🟡 | Ansible start/stop, Compose, systemd units, migrations and CI exist. `requirements.txt` lists only direct dependencies (it was a 255-package freeze whose pins conflicted on a fresh install). There's no lock file yet for fully repeatable installs, e.g. with `pip-compile`. `MODEL` and the Ollama URL are hard-coded. No container image or load test yet. |
| 15 | Privacy and compliance | 🟡 | No customer text is stored ([0005](decisions/0005-no-customer-text-stored.md)). Still needed: telling customers it's a bot, retention periods, data residency, a legal review against the Data Protection Act, and a security review of the balance route, which trusts the WhatsApp number as identity. |

## Known bugs

Found while writing these docs. Each makes a good first change.

- **Spelled-out prices pass the price check.** `checks.run_all("It costs four
  dollars fifty", {4.5})` returns `[]`, because the `_PRICE` regex only
  matches digits. Add a test, then decide whether to match words or block
  them.
- **Input redaction keeps first names.** "Tendai Moyo" becomes
  "Tendai `<PERSON>`". The English spaCy model misses many Zimbabwean names.
- **Nothing checks `price_usd` against the price in `text`** in `knowledge.py`.
  A mismatch blocks every answer about that bundle (it fails safe). Add a
  test that checks every entry.

## Phases

| Phase | What | Status |
|---|---|---|
| 1 | Postgres store: advisory lock, claiming a message by inserting it, migrations, multi-worker tests | ✅ |
| 2 | Conversation lifecycle: open, continue, close; events; agent pickup; sweeper | ✅ |
| 3 | Follow-up context: a structured `context` field on the conversation (last intent, last bundle IDs, pending action) passed into `handle()`, plus multi-turn golden cases | ⬜ next |
| 4 | Metrics: SQL views for the churn signals, and a nightly pseudonymised export (HMAC of the msisdn) to the warehouse | ⬜ |
| 5 | Kafka: inbound and outbound topics keyed by msisdn, plus an outbox from `events` ([0006](decisions/0006-kafka-later.md)) | ⬜ |

## Open decisions

- **Store inbound text?** Today it's never stored
  ([0005](decisions/0005-no-customer-text-stored.md)). The alternative is
  redacted text with a short retention period, which agents and debugging
  would like.
- **Inactivity window:** 24 h by default; is it shorter for this business?
- **Churn definition and data:** which billing or CRM feed defines churn
  (port-out, 90 days inactive…), and who owns joining it to the events.
- **Retention:** for example, messages for 90 days and pseudonymised events for
  longer. Check against the Data Protection Act.
- **Language policy:** hand off non-English messages, or support Shona and
  Ndebele properly.
- **Dev environment:** the Docker engine is missing on the dev laptop, so
  `postgres_mode=docker` can't run there. Reinstall it, or standardise on
  `system`/`external`.
