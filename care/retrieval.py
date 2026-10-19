"""Keyword retrieval over the catalogue.

Docs are scored by token overlap with the question; only docs scoring close to
the best are kept. That cut is a safety control: a second bundle's price must
not become an "allowed" price for the answer just because it scored a little.
"""
import re
from datetime import date

from .knowledge import current_docs

STOPWORDS = {"a", "an", "the", "is", "are", "how", "much", "what", "can", "i", "my", "me",
             "do", "does", "to", "for", "of", "on", "in", "it", "you", "your", "and", "or",
             "please", "bundle", "bundles", "buy", "cost", "costs", "price", "with", "get"}


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOPWORDS}


def score(question_tokens: set[str], doc: dict) -> int:
    title_kw = tokens(doc["title"]) | set(doc["keywords"])
    # Title/keyword hits count double: they say what the doc is *about*.
    return 2 * len(question_tokens & title_kw) + len(question_tokens & tokens(doc["text"]))


def retrieve(question: str, today: date | None = None, k: int = 3, ratio: float = 0.8) -> list[dict]:
    q = tokens(question)
    scored = sorted(((score(q, d), d) for d in current_docs(today)), key=lambda x: -x[0])
    if not scored or scored[0][0] == 0:
        return []
    best = scored[0][0]
    return [d for s, d in scored if s > 0 and s >= ratio * best][:k]
