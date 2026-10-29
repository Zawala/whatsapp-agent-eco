# care-bot

A prototype WhatsApp customer-care assistant for a mobile network. It answers
bundle questions, looks up balances and outages, and logs fault tickets. Anything
risky or unclear goes to a human. It runs fully locally on a small open model
(Mistral NeMo 12B via Ollama).

The point of the project is the guardrails around the model, not the model itself:
the LLM only classifies messages and phrases bundle answers. Everything that
matters is decided by plain code.

> **Sample data only.** Bundle names, prices and backends are invented and are
> not real products or systems.

## How a message is handled

```
message
  -> redact PII (Presidio)             care/guard.py
  -> high-risk rules (regex)           care/router.py   -> human
  -> LLM intent classifier (JSON)      care/router.py   -> human if low confidence / "other"
  -> handler                           care/assistant.py
       balance       -> get_balance tool
       outage        -> get_outage tool
       fault_report  -> create_ticket tool
       bundle_info   -> keyword retrieval + LLM answer
                          -> output checks (prices, policy, canary)   care/checks.py -> human on any violation
  -> redact PII again
  -> reply
```

Any exception at any step becomes a human handoff. Over HTTP (`care/api.py`),
messages from one customer form a conversation stored in Postgres. After a
handoff, the bot stays quiet until an agent releases or closes the
conversation.

### Guardrails

- **High-risk messages never reach the model.** SIM swaps, PIN/PUK/password,
  reversals, refunds and "money back" requests are caught by regex and handed to
  a person.
- **Strict classifier contract.** The classifier must return
  `{"intent", "language", "confidence"}` with allowed values. One retry on bad
  output, then fall back to `unknown` (handoff). Confidence below 0.6 also hands off.
- **Grounded bundle answers.** Retrieval keeps only docs that score close to the
  best match, so a neighbouring bundle's price can't sneak in. Every price in the
  answer must appear in the retrieved docs. Docs carry effective dates, so
  superseded prices are never served.
- **Policy and leak checks.** Replies that promise refunds or guarantees, claim a
  reversal or SIM swap, or contain the system-prompt canary are blocked.
- **Tool allowlist.** The model can only supply the arguments a tool declares.
  Identity, area and idempotency key come from the session. Money-moving tools
  need a human approver. Tools return structured errors and never raise.
- **PII redaction** on input (including names) and output, with custom
  recognisers for Zimbabwean phone numbers and national IDs.
- **Traces record decisions only** (intent, confidence, route, blocked reasons).
  LLM inputs and outputs are hidden from spans.

## Quickstart

Python 3.14 and [Ollama](https://ollama.com).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt     # includes the spaCy en_core_web_lg model
ollama pull mistral-nemo
ollama serve                        # if it isn't already running
python demo.py                      # chat in the terminal
pytest                              # offline tests, no model needed
```

To run the full stack (API, Postgres, sweeper), see
[docs/how-to/run-the-stack.md](docs/how-to/run-the-stack.md).

## Documentation

Start at **[docs/README.md](docs/README.md)**. It has a learning path through
the codebase, and these sections:

| Section | For |
|---|---|
| [Learn](docs/README.md#learn) | Guided, hands-on walkthroughs, in order |
| [How-to](docs/README.md#how-to) | Recipes for changing things: intents, tools, bundles, the model, the schema |
| [Reference](docs/README.md#reference) | Architecture, modules, API, data model, configuration, glossary |
| [Decisions](docs/README.md#decisions) | Why it's built this way |
| [Roadmap](docs/roadmap.md) | What's done, what's next, and the open questions |

## Layout

| Path | What it does |
|---|---|
| `care/assistant.py` | The pipeline and route handlers |
| `care/router.py` | High-risk rules and the intent classifier |
| `care/retrieval.py` | Keyword retrieval with a relative score cut |
| `care/knowledge.py` | Bundle and FAQ catalogue (single source of truth for prices) |
| `care/checks.py` | Deterministic output checks |
| `care/guard.py` | Presidio PII redaction |
| `care/tools.py` | Tool allowlist, session, fake backends |
| `care/llm.py` | The only module that talks to Ollama |
| `care/tracing.py` | Optional Phoenix tracing |
| `care/api.py` | FastAPI app the WhatsApp integration and the agent desk call |
| `care/state.py` | Customers, conversations (open → closed), message outcomes and events, in Postgres |
| `care/sweep.py` | Closes idle conversations (run on a timer) |
| `migrations/` | Alembic migrations for the Postgres schema |
| `compose.yaml` | Postgres for development and tests |
| `deploy/ansible/` | Playbooks that start and stop the whole stack |
| `.github/workflows/` | CI: tests and playbook checks on every push; full-stack evals on a self-hosted runner |
| `demo.py` | Interactive chat in the terminal |
| `scripts/simulate.sh` | Simulated WhatsApp traffic against the API |
| `evals/` | Golden set, eval runner and model comparison |
| `tests/` | Offline, Postgres and live tests |
| `docs/` | Learning guides, how-tos, reference, decisions, roadmap |
