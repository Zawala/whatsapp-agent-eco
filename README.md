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

Any exception at any step becomes a human handoff.

For diagrams of every path (pipeline, sequences, classifier contract, tool
gate, reply outcomes) see [docs/architecture.md](docs/architecture.md). For
what each module does and how to change it, see
[docs/components.md](docs/components.md).

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
| `demo.py` | Interactive chat in the terminal |
| `evals/` | Golden set and the classifier eval runner |
| `tests/` | Offline unit tests and live tests |
| `docs/` | Architecture diagrams and component reference |

## Setup

Developed on Python 3.14. Needs [Ollama](https://ollama.com).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt     # includes the spaCy en_core_web_lg model

ollama pull mistral-nemo
ollama serve                        # if it isn't already running
```

## Usage

Chat with the bot:

```bash
python demo.py
```

Each reply shows its route, e.g. `[route=balance handoff=False blocked=[]]`.
The demo session uses a fake number with a balance and the `harare-cbd` area,
which has a planned-maintenance outage.

With tracing, start Phoenix first, then open http://localhost:6006:

```bash
phoenix serve              # in another terminal
python demo.py --trace
```

If Phoenix isn't running, `--trace` prints connection-refused export errors.
The bot still works; the spans are just dropped.

## Tests

```bash
pytest                         # offline tests use a scripted fake LLM
pytest tests/test_live.py      # live tests, skipped unless Ollama is reachable
```

## Evals

`evals/golden.jsonl` holds labelled messages (`text`, `intent`, `high_risk`).
The runner scores the real classifier against them:

```bash
python -m evals.run                                  # saves to evals/results/
python -m evals.run --baseline evals/results/X.json  # compare with an earlier run
```

It reports intent accuracy (overall and per intent), high-risk recall and
precision, the fallback rate and per-message latency, and lists every miss.
`routed_accuracy` only counts a case when the intent is right and the confidence
is high enough that the pipeline acts on it rather than handing off.

To compare models on speed vs accuracy:

```bash
python -m evals.compare                              # every installed Ollama model
python -m evals.compare --models llama3:8b qwen3:8b
```

It writes `evals/results/compare-<time>.{json,md,html}`: the full results, a
matrix (accuracy, fallback, mean/p50/p95 latency, per intent) and a
speed-vs-accuracy scatter. Models on the Pareto front are flagged, meaning no
other model is both faster and at least as accurate. Each model gets an untimed
warm-up call so load time isn't counted. To switch the bot to the winner, set
`MODEL` in `care/llm.py`. Full details of inputs, metrics and outputs are in
[docs/evals.md](docs/evals.md).

`evals/routes.jsonl` uses the same format with extra phrasings for every route,
including messages that look like high-risk requests but aren't. It's handy for
manual checks in `demo.py`. The runner only reads `golden.jsonl` for now.

## Limitations

- English only. Shona and Ndebele cases still need writing or reviewing by a
  native speaker.
- Backends are in-memory fakes; there is no real WhatsApp integration.
- Retrieval is keyword overlap, not embeddings.
