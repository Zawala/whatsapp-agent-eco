"""Run the router over golden.jsonl with the real LLM and print scores.

    python -m evals.run                      # record a run
    python -m evals.run --baseline FILE      # also compare with an earlier run

The golden set is English only. Shona and Ndebele cases still need adding,
written or reviewed by a native speaker; unreviewed translations don't count.
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path

from care import llm, router

GOLDEN = Path(__file__).with_name("golden.jsonl")
OUT = Path(__file__).with_name("results")


def load_golden() -> list[dict]:
    return [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]


def run() -> dict:
    rows = []
    for case in load_golden():
        flagged = router.is_high_risk(case["text"])
        # Mirror the pipeline: high-risk messages never reach the model.
        pred = {"intent": "high_risk", "language": "-", "confidence": 1.0} if flagged \
            else router.classify(case["text"], llm.chat)
        rows.append({**case, "flagged": flagged, **{f"pred_{k}": v for k, v in pred.items()}})

    routed = [r for r in rows if not r["high_risk"]]
    tp = sum(r["flagged"] and r["high_risk"] for r in rows)
    fp = sum(r["flagged"] and not r["high_risk"] for r in rows)
    fn = sum(not r["flagged"] and r["high_risk"] for r in rows)
    per_intent = Counter()
    per_intent_ok = Counter()
    for r in routed:
        per_intent[r["intent"]] += 1
        per_intent_ok[r["intent"]] += r["pred_intent"] == r["intent"]
    return {
        "model": llm.MODEL,
        "n": len(rows),
        "intent_accuracy": round(sum(per_intent_ok.values()) / max(len(routed), 1), 3),
        "per_intent": {k: f"{per_intent_ok[k]}/{per_intent[k]}" for k in sorted(per_intent)},
        "high_risk_recall": round(tp / max(tp + fn, 1), 3),
        "high_risk_precision": round(tp / max(tp + fp, 1), 3),
        "fallback_rate": round(sum(r["pred_intent"] == "unknown" for r in routed) / max(len(routed), 1), 3),
        "errors": [{"text": r["text"], "want": r["intent"], "got": r["pred_intent"]}
                   for r in routed if r["pred_intent"] != r["intent"]],
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", type=Path)
    args = ap.parse_args()

    res = run()
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(res, indent=2))

    base = json.loads(args.baseline.read_text()) if args.baseline else {}
    for key in ["intent_accuracy", "high_risk_recall", "high_risk_precision", "fallback_rate"]:
        delta = f"  (baseline {base[key]}, {res[key] - base[key]:+.3f})" if key in base else ""
        print(f"{key:22} {res[key]}{delta}")
    print(f"{'per_intent':22} {res['per_intent']}")
    for e in res["errors"]:
        print(f"  MISS want={e['want']:13} got={e['got']:13} {e['text']}")
    print(f"saved {path}")
