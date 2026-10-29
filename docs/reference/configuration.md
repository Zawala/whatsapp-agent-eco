# Configuration

Every setting, where it's read, and its default.

## Environment variables

| Variable | Read by | Default | Meaning |
|---|---|---|---|
| `CARE_API_TOKEN` | `care/api.py` | none (required) | Bearer token for every route except `/health`. The app refuses to start without it. |
| `CARE_DATABASE_URL` | `care/state.py`, `migrations/env.py` | none (required) | libpq URL, e.g. `postgresql://care:care@localhost:5433/care`. Used by the API, `care.sweep` and `alembic`. |
| `CARE_CONVERSATION_WINDOW_HOURS` | `care/state.py` | `24` | A conversation idle longer than this is closed; the next message opens a new one. |
| `CARE_TEST_DATABASE_URL` | `tests/conftest.py` | unset (Postgres tests skip) | A database the tests **wipe**. |
| `CARE_PG_PORT` | `compose.yaml` | `5433` | Host port for the Compose Postgres. |
| `BASE_URL` | `scripts/simulate.sh` | `http://localhost:8000` | API to send simulated traffic to. |
| `CARE_API_TOKEN` | `scripts/simulate.sh` | `dev` | Token the simulation sends. |

`start.yml` writes `CARE_API_TOKEN`, `CARE_DATABASE_URL` and
`CARE_CONVERSATION_WINDOW_HOURS` to `~/.config/care-bot/env`, which the
systemd units load.

## Ansible variables (`deploy/ansible/vars.yml`)

Override any of them with `-e name=value`.

| Variable | Default | Meaning |
|---|---|---|
| `repo_dir` | two levels above the playbook | The checkout to run. |
| `venv` | `<repo_dir>/.venv` | Python venv; created on first run. |
| `config_dir` | `~/.config/care-bot` | Token file and env file (mode 0600). |
| `install_deps` | `false` | `true` reinstalls `requirements.txt`. |
| `postgres_mode` | `docker` | `docker` (compose.yaml), `system` (postgresql service; creates the role and databases), `external` (yours; set `database_url`). |
| `postgres_password` | `care` | Dev only; matches `compose.yaml`. |
| `postgres_port` | `5433` for docker, else `5432` | Also the Compose host port. |
| `database_url` | built from the above | Override for `external`. |
| `ollama_model` | read from `MODEL` in `care/llm.py` | Pulled if missing. |
| `manage_ollama` | `true` | `false`: don't start or stop the service (no sudo); CI uses this. |
| `api_service` | `care-api` | systemd user unit name; the sweeper is `<name>-sweep`. |
| `api_host` | `127.0.0.1` | Bind address. |
| `api_port` | `8000` | |
| `api_workers` | `2` | Each worker loads spaCy (about 1 GB). |
| `conversation_window_hours` | `24` | Written to the env file. |
| `sweep_interval` | `15min` | systemd timer interval for `care.sweep`. |
| `stop_ollama` | `true` | `stop.yml` stops Ollama (if `manage_ollama`). |
| `stop_system_postgres` | `false` | `stop.yml` stops a `system` Postgres. |

The CI evals job overrides `manage_ollama=false`, `api_service=care-api-ci`,
`api_port=8100`, `postgres_port=5434`, `install_deps=true`, and puts `venv`
and `config_dir` outside the checkout (`.github/workflows/evals.yml`).

## Constants in code

These are deliberately not settings, because changing them changes behaviour
that's tested and evaluated. Change them in code, then rerun the tests and
the eval.

| Constant | Where | Value |
|---|---|---|
| `MODEL` | `care/llm.py` | `mistral-nemo:latest` |
| Ollama URL, timeout, temperature | `care/llm.py` | `localhost:11434/v1`, 60 s, 0 |
| `CONF_MIN` | `care/router.py` | `0.6` |
| `HIGH_RISK`, `INTENTS`, `LANGUAGES` | `care/router.py` | see [components](components.md#carerouterpy--high-risk-rules-and-intent-classifier) |
| retrieval `k`, `ratio` | `care/retrieval.py` | `3`, `0.8` |
| `CANARY` | `care/checks.py` | `CANARY-7f3a` |
| `Store(max_size=10)` | `care/state.py` | in-flight messages per worker (one DB connection each) |
| text limit | `care/api.py` | 4096 characters (WhatsApp's limit) |
| `--min-routed-accuracy` | `.github/workflows/evals.yml` | `0.95` |
