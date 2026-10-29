# Run the tests

| Suite | Needs | Command |
|---|---|---|
| Offline (`test_core.py`) | nothing; uses `FakeLLM` | `pytest` |
| Postgres (`test_api.py`) | a database the tests may **wipe** | `CARE_TEST_DATABASE_URL=postgresql://care:care@localhost:5433/care_test pytest tests/test_api.py` |
| Live (`test_live.py`) | Ollama with the model | `pytest tests/test_live.py` |
| Eval gate | Ollama with the model | `python -m evals.run --min-routed-accuracy 0.95` |

Plain `pytest` runs everything it can. The Postgres tests skip themselves
without `CARE_TEST_DATABASE_URL`, and the live tests skip without Ollama.
`pytest -ra` lists every skip and its reason.

**Never point `CARE_TEST_DATABASE_URL` at a database you care about.** The
`db_url` fixture in [`tests/conftest.py`](../../tests/conftest.py) migrates it
to the latest revision, then truncates every table before each test. If you
have no Postgres, use the throwaway cluster in
[run the stack](run-the-stack.md#postgres).

## How the tests are built

- **`FakeLLM(*replies)`** in `test_core.py` returns scripted replies in order
  and counts calls. That's how tests assert things like "a high-risk message
  makes zero model calls". Use `route_json(intent, conf)` for classifier
  replies.
- **`make_client(llm)`** in `test_api.py` builds a separate app with its own
  connection pool on each call, which stands in for a separate worker. The
  concurrency tests use two of them, with **`SlowLLM`**, a thread-safe fake
  that sleeps and records how many calls overlap.
- **`age(db_url, hours)`** moves `last_activity_at` back, to test the idle
  window without waiting.
- **`migrate(url, revision, down=False)`** in `conftest.py` steps the schema,
  for the migration tests.

CI runs the offline and Postgres suites on every push ([CI](ci.md)).
