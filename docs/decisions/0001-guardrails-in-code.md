# 0001: Guardrails live in code; the model only classifies and phrases

**Status:** accepted (from the start of the project)

## Context

The bot handles money-adjacent requests (balances, refunds, SIM swaps) for a
mobile network. Small open models get prompt-injected, make up prices, and
promise things. A care bot that does any of those is worse than no bot.

## Decision

The model does exactly two jobs:

- classify the message into a fixed set of intents, as strict JSON;
- phrase bundle answers from catalogue text it's handed.

Everything that matters is decided by ordinary, testable code around it:

- **High-risk requests** are caught by regex, before the model.
- **The classifier's output** is validated against a schema, with one retry,
  then a fallback that hands off. Low confidence hands off too.
- **Tool replies** use fixed templates. Tools get their identity and arguments
  from the session, behind an allowlist.
- **Model-written answers** are checked: prices against the retrieved
  catalogue entries, banned phrases, and the system-prompt canary.
- **Any failure, anywhere,** becomes a handoff to a human.

## Consequences

- **The bot is narrow.** Anything outside the intents goes to a person. That's
  intended; the handoff rate is a metric to watch, not a bug.
- **Every guardrail is unit-testable** with a fake model (`FakeLLM`), so
  `test_core.py` runs in seconds and needs no model.
- **Swapping models is cheap and low-risk:** a new model can only be
  misclassifying (caught by the eval gate), never unsafe in new ways.
- **Changing what the bot can do means code changes:** see
  [add an intent](../how-to/add-an-intent.md) and [add a tool](../how-to/add-a-tool.md).

See [architecture §2](../reference/architecture.md#2-message-pipeline) and
[learn: guardrails](../learn/03-guardrails.md).
