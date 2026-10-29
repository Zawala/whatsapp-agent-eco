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
| `care/api.py` | FastAPI app the WhatsApp integration calls |
| `care/state.py` | Conversation state (bot or agent) and message dedupe, in Postgres |
| `migrations/` | Alembic migrations for the Postgres schema |
| `compose.yaml` | Postgres for development and tests |
| `deploy/ansible/` | Playbooks that start and stop the whole stack |
| `.github/workflows/` | CI: tests and playbook checks on every push; full-stack evals on a self-hosted runner |
| `demo.py` | Interactive chat in the terminal |
| `scripts/simulate.sh` | Simulated WhatsApp traffic against the API |
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

Or run it as an API. It needs Postgres, which `compose.yaml` provides on port
5433 with a `care` database and a `care_test` database for the tests:

```bash
docker compose up -d --wait
export CARE_DATABASE_URL=postgresql://care:care@localhost:5433/care
alembic upgrade head
```

To use a Postgres you already run instead, create the role and databases
(`CREATE ROLE care LOGIN PASSWORD 'care'`, then `CREATE DATABASE care OWNER
care` and the same for `care_test`) and point the URLs at it.

Then:

```bash
CARE_API_TOKEN=dev uvicorn care.api:create_app --factory --port 8000 --workers 4

curl -X POST localhost:8000/messages -H 'Authorization: Bearer dev' \
  -H 'content-type: application/json' \
  -d '{"message_id":"wamid.1","msisdn":"263772000001","area":"harare-cbd","text":"What is my balance?"}'
```

Any handoff gives the conversation to a human agent. After that the bot stays
quiet (`reply: null`) until `POST /conversations/{msisdn}/release`.
A repeated `message_id` returns the stored result and isn't processed again.
Workers share the database. One customer's messages run one at a time, while
different customers run in parallel. See
[docs/components.md](docs/components.md#careapipy--http-api) for the endpoints.

With the API running, `scripts/simulate.sh` walks through every route, a
redelivered message, a handoff, the bot staying quiet, and a release.
`scripts/simulate.sh load 10` sends 10 bundle questions at once.

The demo and the simulation use a fake number with a balance and the
`harare-cbd` area, which has a planned-maintenance outage.

With tracing, start Phoenix first, then open http://localhost:6006:

```bash
phoenix serve              # in another terminal
python demo.py --trace
```

If Phoenix isn't running, `--trace` prints connection-refused export errors.
The bot still works; the spans are just dropped.

## Start and stop everything

`deploy/ansible/start.yml` brings the whole stack up in order:

1. creates the venv on first run;
2. starts Ollama and pulls the model named in `care/llm.py` if it's missing;
3. starts Postgres;
4. runs the migrations;
5. starts the API as the systemd user service `care-api`;
6. waits for `/health`.

`stop.yml` shuts it down in reverse order and keeps all data.

```bash
ansible-playbook -i deploy/ansible/inventory.ini deploy/ansible/start.yml
ansible-playbook -i deploy/ansible/inventory.ini deploy/ansible/stop.yml
```

- Postgres comes from `compose.yaml` by default. Add `-e postgres_mode=system`
  to use the machine's own Postgres; the playbook creates the `care` role and
  the databases if they're missing.
- `stop.yml` also stops Ollama; add `-e stop_ollama=false` to leave it running.
  A system Postgres is left running unless you add `-e stop_system_postgres=true`.
- The Ollama and system Postgres services need sudo. Add `-K` if sudo asks for a
  password.
- The API token is generated once and kept in `~/.config/care-bot/api_token`
  (mode 0600). Logs: `journalctl --user -u care-api -f`.
- The API is restarted on every `start.yml` run, so it picks up code changes.
  Add `-e install_deps=true` to reinstall the requirements.

Other settings (port, workers, password) are in `deploy/ansible/vars.yml`.

## Tests

```bash
pytest                         # offline tests use a scripted fake LLM
pytest tests/test_live.py      # live tests, skipped unless Ollama is reachable
CARE_TEST_DATABASE_URL=postgresql://care:care@localhost:5433/care_test pytest tests/test_api.py
                               # state/API tests; this database gets wiped
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

## CI

| Workflow | Runs on | When | What fails it |
|---|---|---|---|
| `.github/workflows/ci.yml` | GitHub-hosted | every push to `main` and every PR | a unit or Postgres test (against a Postgres service container); a playbook syntax or `ansible-lint` error; an invalid `compose.yaml` |
| `.github/workflows/evals.yml` | self-hosted, label `ollama` | PRs touching `care/`, `evals/`, `deploy/`, migrations, compose or requirements; pushes to `main`; nightly at 02:00 UTC; manual | `start.yml` failing; routed accuracy below 0.95 or any high-risk miss; a live prompt-injection test; the API not answering simulated traffic; `stop.yml` failing |

The evals job runs the real stack: `start.yml`, then the golden set with
`--min-routed-accuracy`, then `tests/test_live.py`, then `scripts/simulate.sh`
against the API, then `stop.yml` (which always runs). The scores go in the job
summary and the results are uploaded as an artifact. It uses its own service
name, ports (API 8100, Postgres 5434) and venv, so it can run on the same
machine as a dev stack. It never runs for pull requests from forks, because a
self-hosted runner would execute their code on your machine.

The model evals need a self-hosted runner. GitHub's standard runners have no
GPU, private-repo runners have too little memory for mistral-nemo's 7 GB, and
the model would have to be downloaded on every run.

**One-off setup for the self-hosted runner** (on the machine with Ollama):

1. Install the Docker engine, Ansible (with the `community.docker` collection)
   and Python 3.14, and have Ollama running with the model pulled.
2. Add a runner under GitHub → repo Settings → Actions → Runners → New
   self-hosted runner, and give it the extra label `ollama`.
3. Run `sudo loginctl enable-linger <runner user>`, so the runner can use
   `systemctl --user` without anyone logged in.
4. Under Settings → Actions → General, require approval for workflows from
   outside collaborators.

If the runner is offline, evals jobs wait in the queue. GitHub cancels them
after 24 hours.

To change the bar, run the workflow by hand with a different
`min_routed_accuracy`, or edit the default in `evals.yml`.

## Limitations

- English only. Shona and Ndebele cases still need writing or reviewing by a
  native speaker.
- Backends are in-memory fakes; there is no real WhatsApp integration.
- Retrieval is keyword overlap, not embeddings.
