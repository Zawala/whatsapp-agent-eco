# Debug a reply

"Why did the bot say that?" or "why did it hand off?" Work from the cheapest
clue to the most detailed.

## 1. Read `route` and `blocked`

Every reply carries them: the API response, the `[route=… blocked=…]` line in
`demo.py`, and the `messages` table. Look the pair up in the
[reply outcomes table](../reference/architecture.md#7-reply-outcomes):

| You see | It means | Look at |
|---|---|---|
| `route=high_risk` | The regex matched; the model was never called | `HIGH_RISK` in `router.py` |
| `route=other` or `unknown` | The classifier said `other`, or failed twice | the classifier prompt; `validate` |
| `route=<intent>`, `handoff`, no `blocked` | Confidence below 0.6 | the `confidence` column; `CONF_MIN` |
| `blocked=["tool:<error>"]` | The tool gate or backend refused | `call_tool` errors in `tools.py` |
| `blocked=["no_context"]` | Retrieval found no catalogue entry | keywords in `knowledge.py` |
| `blocked=["price_not_in_context:…"]` | The model quoted a price that wasn't in the context | which docs were retrieved (step 3) |
| `blocked=["banned:…"]`, `["canary_leak"]` | The output checks caught the answer | `checks.py` |
| `route=error`, `["exception:<Type>"]` | Something raised | the API log, or the trace's exception |
| `route=agent`, `reply: null` | An agent owns the conversation | step 2 |

## 2. Check the conversation

```bash
curl -s localhost:8000/conversations/<msisdn> -H "Authorization: Bearer $CARE_API_TOKEN" | jq
```

This shows the status, who has it, the handoff reason, the last messages (with
intent, confidence and latency), and every event. A customer "ignored" by the
bot is usually in an `agent` conversation that nobody picked up.

## 3. Reproduce it offline

Run the classifier and retrieval on the same text:

```python
from care import router, retrieval, llm
router.is_high_risk(text)
router.classify(text, llm.chat)                  # the real model
[d["id"] for d in retrieval.retrieve(text)]
```

To rule out the model, replay the pipeline with a fake that returns what you
suspect it said. See [guardrails, step 2](../learn/03-guardrails.md#step-2-a-made-up-price).
Once you understand it, turn it into a test in `test_core.py`, and add the
message to `evals/golden.jsonl` if the classifier got it wrong.

## 4. Traces

`python demo.py --trace` with `phoenix serve` running records each decision:
intent, language, confidence, `retrieval.doc_ids`, route, blocked, and
exceptions. Message text and model output are deliberately not recorded, so
traces tell you *what* was decided, and steps 1–3 tell you *why*.
