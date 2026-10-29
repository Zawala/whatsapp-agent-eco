# Change bundles and prices

[`care/knowledge.py`](../../care/knowledge.py) is the single source of truth
for prices. The model may only quote prices from the entries retrieval hands
it, and [`checks.check_prices`](../../care/checks.py) blocks any other price.

## Change a price

Don't edit the price in place. Add a new entry, and end the old one:

```python
{"id": "data-5gb-2027", "title": "5GB Weekly Data Bundle", "price_usd": 5.00,
 "keywords": ["5gb", "weekly", "data"],
 "text": "5GB of data valid for 7 days. Costs $5.00. Dial *143# to buy.",
 "effective_from": date(2027, 1, 1), "expires": None},
# and on the current data-5gb entry:
 "expires": date(2026, 12, 31)
```

`current_docs(today)` serves only the entries in effect today. That keeps the
history and lets you schedule a change ahead of time. `data-5gb-old` is an
example that has already expired.

## Add a bundle or FAQ

Same shape. FAQs have no `price_usd`.

- **`keywords`:** the words people actually use. Retrieval scores
  title and keyword hits double. Generic words such as "bundle", "price" and
  "buy" are stopwords in [`retrieval.py`](../../care/retrieval.py), so they
  don't help.
- **`price_usd` must match the price in `text`.** The allowed prices come from
  `price_usd`, but the model reads `text`. If they differ, every answer about
  that bundle is blocked. That fails safe, but nothing tests it yet. A test
  that checks every entry would be a good first contribution.

## Check it

```python
from datetime import date
from care.retrieval import retrieve
[d["id"] for d in retrieve("How much is the 5GB bundle?")]                     # just the one you expect
[d["id"] for d in retrieve("How much is the 5GB bundle?", today=date(2027, 1, 2))]   # the new entry
```

```bash
pytest tests/test_core.py -k "retrieve or stale or price"
```

Retrieval's 80% cut is a safety control. If a question returns a neighbouring
bundle, that bundle's price becomes "allowed" too. Fix it with better keywords;
don't loosen the cut.
