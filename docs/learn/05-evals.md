# 5. Evals and choosing a model

**Goal:** measure the classifier, add a case, and understand how the model was
chosen.
**You need:** Ollama running with `mistral-nemo` pulled. A full run takes
about 5 minutes on CPU.

## Step 1: score the classifier

```bash
python -m evals.run
```

```
intent_accuracy        1.0
routed_accuracy        1.0
high_risk_recall       1.0
high_risk_precision    1.0
fallback_rate          0.0
latency_mean_s         12.046
latency_p95_s          13.137
per_intent             {'balance': '4/4', 'bundle_info': '7/7', 'fault_report': '4/4', 'other': '6/6', 'outage': '3/3'}
saved evals/results/20261029-103047.json
```

**What it measures:** [`evals/run.py`](../../evals/run.py) runs every case in
[`evals/golden.jsonl`](../../evals/golden.jsonl) through the high-risk regex
and, for the rest, the real classifier, exactly as the pipeline does.

- **`routed_accuracy`** is the number that matters. It counts a case only if
  the intent is right *and* confident enough (≥ 0.6) that the bot acts on it.
- **`high_risk_*`** scores the regex alone, so it's the same for every model.
- **Every miss** is listed with what was wanted and what came back.

All metrics are in [the evals reference](../reference/evals.md#what-it-measures).

## Step 2: compare against a baseline

```bash
python -m evals.run --baseline evals/results/20261029-103047.json
```

Each metric now shows its change from the baseline. Use this whenever you
change the classifier prompt in `router.SYSTEM`.

## Step 3: add a golden case

The golden set is 31 cases, and 24 of them reach the model. One case moves
accuracy by about 4 points, so it needs to grow. Add one line to
`evals/golden.jsonl`:

```json
{"text": "my data is not working since I topped up", "intent": "fault_report", "high_risk": false}
```

Then:

```bash
pytest tests/test_core.py -k golden    # the regex must agree with the high_risk label
python -m evals.run                    # does the model get it?
```

Good cases are real phrasings (redacted), near-misses between intents ("no
signal" could be outage or fault), and look-alikes of high-risk requests that
aren't (`evals/routes.jsonl` has many). Shona and Ndebele cases need a native
speaker to write or review them.

## Step 4: the CI gate

```bash
python -m evals.run --min-routed-accuracy 0.95; echo "exit=$?"
```

This exits 1 if routed accuracy drops below 0.95 or any high-risk case is
missed. The `evals.yml` workflow runs it on every PR that touches the model
code ([CI](../how-to/ci.md)).

## Step 5: compare models

```bash
python -m evals.compare --models llama3:8b mistral-nemo:latest
```

This writes a matrix and a speed-vs-accuracy scatter to `evals/results/`. The
**Pareto front** is the set of models that no other model beats on both speed
and accuracy, so the real choice is between those.

[Decision 0002](../decisions/0002-local-model-mistral-nemo.md) has the full
2026-10-29 run across six models, and why `mistral-nemo` was chosen over the
faster `llama3:8b`.

## Exercises

1. Add three golden cases that sit between `outage` and `fault_report`. Does
   `routed_accuracy` still hold?
2. Change `CONF_MIN` in `router.py` to 0.9 and rerun the eval. What happens to
   `routed_accuracy`, and why? (Then change it back.)
3. Run the comparison with `qwen3:8b`. Why does it fail at the warm-up step?
   (See the notes in [decision 0002](../decisions/0002-local-model-mistral-nemo.md).)

**Next:** pick an item from the [roadmap](../roadmap.md), and use the
[how-tos](../README.md#how-to).
