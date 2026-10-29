# Add an intent

Example: a `roaming` intent for "Does my bundle work in South Africa?".

## Files to touch, in order

1. **[`care/router.py`](../../care/router.py), `INTENTS`:** add `"roaming"`.
   `validate` rejects any intent not in this set, so the model can't invent
   one.
2. **`router.SYSTEM`:** add a line describing it, in the same style as the
   others, and say how it differs from its neighbours (`bundle_info` in this
   case). The model only knows what the prompt tells it.
3. **[`care/assistant.py`](../../care/assistant.py), `_dispatch`:** add a
   branch that calls a new handler:
   - **Answer from a tool** (like `_balance`): call `call_tool(...)`, hand off
     on `not r["ok"]` with `blocked=[f"tool:{r['error']}"]`, otherwise fill a
     fixed template. Needs a tool; see [add a tool](add-a-tool.md).
   - **Answer from the catalogue** (like `_bundle`): add catalogue entries
     instead, and route the intent to `_bundle`.
   - **Always hand off** (simplest): `return _handoff("roaming")`.

   An intent with no branch falls through to `_bundle`, which is the default
   at the end of `_dispatch`. Always add a branch on purpose.
4. **If the intent can be high-risk:** add the phrases to `HIGH_RISK` in
   `router.py` instead. Those never reach the model.
5. **[`evals/golden.jsonl`](../../evals/golden.jsonl):** add at least three
   cases for the new intent, plus near-misses for its neighbours (for example,
   bundle questions that mention travel but aren't about roaming). Add more
   phrasings to `evals/routes.jsonl` for manual testing.
6. **[`tests/test_core.py`](../../tests/test_core.py):** one test per
   outcome, with `FakeLLM(route_json("roaming"))`, following
   `test_balance_uses_template_and_session_identity`. Assert the reply text,
   `route`, `handoff`, and `fake.calls`.
7. **Docs:**
   - the reply-outcomes table in
     [architecture §7](../reference/architecture.md#7-reply-outcomes);
   - `INTENTS` in [components](../reference/components.md#carerouterpy--high-risk-rules-and-intent-classifier);
   - the classifier contract in
     [architecture §5](../reference/architecture.md#5-classifier-contract).

## Check it

```bash
pytest                                     # includes the golden-label regex check
python -m evals.run --min-routed-accuracy 0.95   # did the prompt change hurt other intents?
```

Compare `per_intent` with the last run (`--baseline evals/results/<file>.json`).
A new intent often takes accuracy from its nearest neighbour.
