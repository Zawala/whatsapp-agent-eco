# Change the model

The model is set in one place: `MODEL` in [`care/llm.py`](../../care/llm.py).
Everything else takes an `llm` callable, so swapping it touches nothing else.

## Steps

1. **Pull the candidate:** `ollama pull <model>`.
2. **Compare it with the current one, on the same machine:**

   ```bash
   python -m evals.compare --models mistral-nemo:latest <model>
   ```

   Read the matrix and the Pareto front ([evals reference](../reference/evals.md#reading-the-results)).
   With 24 scored cases, a one-case gap is noise; add golden cases before
   relying on it.
3. **Switch:** set `MODEL` in `care/llm.py`. `start.yml` reads the name from
   there and pulls it if it's missing.
4. **Check:**

   ```bash
   python -m evals.run --min-routed-accuracy 0.95
   pytest tests/test_live.py              # prompt injection ×3 against the real model
   ```

5. **Record it:** add a decision record (copy
   [0002](../decisions/0002-local-model-mistral-nemo.md)), with the run's
   table, the misses, and why. Update the model name in the README and in
   [architecture §1](../reference/architecture.md#1-system-context).

## Things that bite

- **Reasoning models** (`qwen3`, `gpt-oss`) think before answering. On CPU that
  exceeds the 60 s timeout in `llm.py`, which counts as an error and a
  handoff. Turn thinking off, or don't use them for classification.
- **Pin the version.** A `:latest` tag can change under you on the next pull.
  Pin the digest you tested, and rerun the eval before any upgrade. The
  nightly `evals.yml` run catches drift.
- **A different provider:** anything with the signature
  `chat(messages, json_mode=False) -> str` works. It must honour JSON mode, and
  run at temperature 0 for repeatable evals.
