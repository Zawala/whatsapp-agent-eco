# CI and the self-hosted runner

| Workflow | Runs on | When | What fails it |
|---|---|---|---|
| [`ci.yml`](../../.github/workflows/ci.yml) | GitHub-hosted | every push to `main` and every PR | a unit or Postgres test (against a Postgres service container); a playbook syntax or `ansible-lint` error; an invalid `compose.yaml` |
| [`evals.yml`](../../.github/workflows/evals.yml) | self-hosted, label `ollama` | PRs touching `care/`, `evals/`, `deploy/`, migrations, compose or requirements; pushes to `main`; nightly at 02:00 UTC; manual | `start.yml` failing; routed accuracy below 0.95 or any high-risk miss; a live prompt-injection test; the API not answering simulated traffic; `stop.yml` failing |

The evals job runs the real stack: `start.yml`, then the golden set with
`--min-routed-accuracy`, then `tests/test_live.py`, then `scripts/simulate.sh`
against the API, then `stop.yml` (which always runs). The scores go in the job
summary and the results are uploaded as an artifact.

It uses its own service name, ports (API 8100, Postgres 5434) and venv, so it
can run on the same machine as a dev stack. It never runs for pull requests
from forks, because a self-hosted runner would execute their code on your
machine.

**Why self-hosted:** GitHub's standard runners have no GPU, private-repo
runners have too little memory for mistral-nemo's 7 GB, and the model would
have to be downloaded on every run.

## One-off setup for the self-hosted runner

On the machine with Ollama:

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

## Changing it

- **The accuracy bar:** run the workflow by hand with a different
  `min_routed_accuracy`, or edit the default in `evals.yml`.
- **Lint the workflows locally:** `actionlint`. The custom runner label is
  declared in `.github/actionlint.yaml`.
- **Lint the playbooks locally:**
  `ansible-lint --profile production deploy/ansible/start.yml deploy/ansible/stop.yml`.
