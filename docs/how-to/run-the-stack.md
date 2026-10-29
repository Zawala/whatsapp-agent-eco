# Run the stack

The full stack is Ollama, Postgres, the API (`care/api.py`) and the sweeper
timer (`care/sweep.py`). You can start it by hand or with Ansible.

## Postgres

Pick one:

- **Docker:** `docker compose up -d --wait` starts Postgres 18 on
  `127.0.0.1:5433`, with a `care` database and a `care_test` database for the
  tests. Change the host port with `CARE_PG_PORT`.
- **A Postgres you already run:** create the role and databases once:

  ```sql
  CREATE ROLE care LOGIN PASSWORD 'care';
  CREATE DATABASE care OWNER care;
  CREATE DATABASE care_test OWNER care;
  ```

- **A throwaway cluster, no root needed** (uses the installed Postgres
  binaries; data lives wherever you point `-D`):

  ```bash
  B=/usr/lib/postgresql/18/bin; D=/tmp/care-pg
  echo care > /tmp/pw && $B/initdb -D $D -U care --pwfile=/tmp/pw -A scram-sha-256
  $B/pg_ctl -D $D -o "-p 5499 -k /tmp -c listen_addresses=127.0.0.1" -l $D.log start
  PGPASSWORD=care $B/createdb -h 127.0.0.1 -p 5499 -U care care
  PGPASSWORD=care $B/createdb -h 127.0.0.1 -p 5499 -U care care_test
  # stop:  $B/pg_ctl -D $D stop
  ```

Then apply the schema:

```bash
export CARE_DATABASE_URL=postgresql://care:care@localhost:5433/care
alembic upgrade head
```

## By hand

```bash
CARE_API_TOKEN=dev uvicorn care.api:create_app --factory --port 8000 --workers 2
python -m care.sweep        # closes idle conversations; run it on a schedule
scripts/simulate.sh         # in another terminal, to see it working
```

The app refuses to start without `CARE_API_TOKEN` and `CARE_DATABASE_URL`.
Every setting is in [configuration](../reference/configuration.md).

## With Ansible

`deploy/ansible/start.yml` brings everything up in order, and `stop.yml`
shuts it down in reverse order:

```bash
ansible-playbook -i deploy/ansible/inventory.ini deploy/ansible/start.yml
ansible-playbook -i deploy/ansible/inventory.ini deploy/ansible/stop.yml
```

`start.yml`:

1. creates the venv on first run;
2. starts Ollama and pulls the model named in `care/llm.py` if it's missing;
3. starts Postgres;
4. runs `alembic upgrade head`;
5. writes the token and env file to `~/.config/care-bot/` (mode 0600);
6. starts the API (`care-api`) and the sweeper timer (`care-api-sweep.timer`)
   as systemd user services;
7. waits for `/health`.

It's safe to rerun. The API is always restarted, so it picks up code changes.

`stop.yml` stops the timer and the API, then Postgres, then Ollama. It keeps
all data: the Docker volume stays and nothing is dropped.

**Options** (pass with `-e`; all are in `deploy/ansible/vars.yml`):

| Option | Effect |
|---|---|
| `postgres_mode=docker` (default) | Postgres from `compose.yaml`. Needs the Docker engine. |
| `postgres_mode=system` | The machine's postgresql service; creates the role and databases if they're missing. |
| `postgres_mode=external database_url=postgresql://…` | A Postgres you run, e.g. a managed one. Nothing is started or stopped for it. |
| `manage_ollama=false` | Don't start or stop the Ollama service, just expect it to be running. No sudo needed. |
| `stop_ollama=false` | `stop.yml` leaves Ollama running. |
| `stop_system_postgres=true` | `stop.yml` also stops a system Postgres (left running by default). |
| `install_deps=true` | Reinstall `requirements.txt` into the venv. |
| `api_service`, `api_port`, `config_dir`, `postgres_port` | Run a second stack beside the first; CI does this. |

The Ollama and system Postgres services need sudo; add `-K` if sudo asks for
a password.

Logs: `journalctl --user -u care-api -f` (sweeper: `-u care-api-sweep`).
To try it: `CARE_API_TOKEN=$(cat ~/.config/care-bot/api_token) scripts/simulate.sh`.
