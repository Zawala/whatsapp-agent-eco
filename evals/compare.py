"""Run the golden set against several Ollama models and compare speed vs accuracy.

    python -m evals.compare                             # every model `ollama list` shows
    python -m evals.compare --models llama3:8b qwen3:8b

Each model gets one untimed warm-up call first, so model load time isn't counted.
Writes evals/results/compare-<time>.json (full per-model results), a markdown
matrix and a speed-vs-accuracy scatter (HTML) next to it.
"""
import argparse
import json
import time

import httpx

from care import llm, router
from evals.run import OUT, run

OLLAMA = "http://localhost:11434"


def installed_models() -> list[str]:
    return sorted(m["name"] for m in httpx.get(f"{OLLAMA}/api/tags", timeout=10).json()["models"])


def evaluate(model: str) -> dict:
    llm.MODEL = model
    try:
        router.classify("How much is the 5GB bundle?", llm.chat)  # warm-up: load the model
    except Exception as e:
        return {"model": model, "failed": f"{type(e).__name__}: {e}"}
    start = time.perf_counter()
    res = run()
    res["wall_s"] = round(time.perf_counter() - start, 1)
    return res


def pareto(results: list[dict]) -> set[str]:
    """Models no other model beats on both accuracy and mean latency."""
    return {a["model"] for a in results
            if not any(b["routed_accuracy"] >= a["routed_accuracy"] and b["latency_mean_s"] <= a["latency_mean_s"]
                       and (b["routed_accuracy"], b["latency_mean_s"]) != (a["routed_accuracy"], a["latency_mean_s"])
                       for b in results)}


def matrix(results: list[dict], failed: list[dict]) -> str:
    front = pareto(results)
    intents = sorted({k for r in results for k in r["per_intent"]})
    head = ["model", "routed acc", "intent acc", "fallback", "errors", "mean s", "p50 s", "p95 s", "msgs/min",
            *intents, "pareto"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in sorted(results, key=lambda r: (-r["routed_accuracy"], r["latency_mean_s"])):
        lines.append("| " + " | ".join(map(str, [
            r["model"], r["routed_accuracy"], r["intent_accuracy"], r["fallback_rate"], r["call_errors"],
            r["latency_mean_s"], r["latency_p50_s"], r["latency_p95_s"],
            round(60 / r["latency_mean_s"], 1) if r["latency_mean_s"] else "-",
            *(r["per_intent"].get(i, "-") for i in intents),
            "yes" if r["model"] in front else ""])) + " |")
    lines += [f"| {f['model']} | failed: {f['failed']} |" for f in failed]
    return "\n".join(lines)


def scatter(results: list[dict], path) -> None:
    import plotly.graph_objects as go

    front = pareto(results)
    fig = go.Figure()
    for on_front in (True, False):
        pts = [r for r in results if (r["model"] in front) == on_front]
        fig.add_trace(go.Scatter(
            x=[r["latency_mean_s"] for r in pts], y=[r["routed_accuracy"] for r in pts],
            text=[r["model"] for r in pts], mode="markers+text", textposition="top center",
            name="Pareto front" if on_front else "dominated", marker={"size": 12},
            error_x={"type": "data", "symmetric": False,
                     "array": [r["latency_p95_s"] - r["latency_mean_s"] for r in pts]},
            hovertemplate="%{text}<br>mean %{x:.2f}s<br>routed acc %{y:.3f}<extra></extra>"))
    fig.update_layout(title="Classifier: speed vs accuracy (golden set)",
                      xaxis_title="mean latency per message, s (bar to p95, lower is better)",
                      yaxis_title="routed accuracy (correct and confidence >= %.1f)" % router.CONF_MIN,
                      yaxis_range=[0, 1.05], template="plotly_white")
    fig.write_html(path, include_plotlyjs="cdn")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", help="default: every installed model")
    args = ap.parse_args()

    results, failed = [], []
    for model in args.models or installed_models():
        print(f"running {model} ...", flush=True)
        res = evaluate(model)
        if "failed" in res:
            failed.append(res)
            print(f"  failed: {res['failed']}")
        else:
            results.append(res)
            print(f"  routed_accuracy={res['routed_accuracy']} mean={res['latency_mean_s']}s "
                  f"p95={res['latency_p95_s']}s errors={res['call_errors']}")

    OUT.mkdir(exist_ok=True)
    stem = OUT / f"compare-{time.strftime('%Y%m%d-%H%M%S')}"
    stem.with_suffix(".json").write_text(json.dumps(results + failed, indent=2))
    table = matrix(results, failed) if results else "no model completed"
    stem.with_suffix(".md").write_text(table + "\n")
    if results:
        scatter(results, stem.with_suffix(".html"))
        best = max(results, key=lambda r: (r["routed_accuracy"], -r["latency_mean_s"]))
        print(f"\n{table}\n\nmost accurate: {best['model']} "
              f"(routed_accuracy {best['routed_accuracy']}, {best['latency_mean_s']}s/msg)")
        print(f"pareto front (best trade-offs): {', '.join(sorted(pareto(results)))}")
    print(f"saved {stem}.json / .md / .html")
