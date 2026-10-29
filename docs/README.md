# care-bot documentation

Four kinds of document, each with one job:

- **Learn:** guided walkthroughs you do in order. Each one runs real
  commands and explains what happened.
- **How-to:** recipes for one change: which files to touch, in which order,
  and how to check it worked.
- **Reference:** facts to look up: modules, endpoints, tables, settings, terms.
- **Decisions:** why it's built this way, and what each choice costs.

## Learning path

Roughly half a day. Each step builds on the last.

| # | Do | Then read |
|---|---|---|
| 1 | [Run it](learn/01-run-it.md): the demo, the API, simulated traffic | [Architecture §1–2](reference/architecture.md#1-system-context) |
| 2 | [Follow a message](learn/02-follow-a-message.md) through the code | [Components](reference/components.md), [Decision 0001](decisions/0001-guardrails-in-code.md) |
| 3 | [Try to break the guardrails](learn/03-guardrails.md) | [Architecture §5–7](reference/architecture.md#5-classifier-contract) |
| 4 | [Conversations and concurrency](learn/04-conversations.md), with curl and SQL | [Data model](reference/data-model.md), [Decisions 0003–0005](decisions/0003-postgres-over-mongo.md) |
| 5 | [Evals](learn/05-evals.md): score the classifier, add a case, compare models | [Evals reference](reference/evals.md), [Decision 0002](decisions/0002-local-model-mistral-nemo.md) |

After that, pick something from the [roadmap](roadmap.md) and use the how-tos.

## Learn

1. [Run it](learn/01-run-it.md)
2. [Follow a message through the code](learn/02-follow-a-message.md)
3. [Try to break the guardrails](learn/03-guardrails.md)
4. [Conversations and concurrency](learn/04-conversations.md)
5. [Evals and choosing a model](learn/05-evals.md)

## How-to

Changing behaviour:

- [Add an intent](how-to/add-an-intent.md)
- [Add a tool](how-to/add-a-tool.md)
- [Change bundles and prices](how-to/change-bundles.md)
- [Change the model](how-to/change-the-model.md)
- [Add a database migration](how-to/add-a-migration.md)

Running and debugging:

- [Run the stack](how-to/run-the-stack.md) (API, Postgres, sweeper, Ansible)
- [Run the tests](how-to/run-tests.md)
- [CI and the self-hosted runner](how-to/ci.md)
- [Debug a reply](how-to/debug-a-reply.md): why did the bot say that, or hand off?

## Reference

- [Architecture](reference/architecture.md): diagrams of every path
- [Components](reference/components.md): each module, what it exposes, design notes
- [HTTP API](reference/api.md): endpoints, shapes, status codes
- [Data model](reference/data-model.md): tables, conversation lifecycle, locking
- [Configuration](reference/configuration.md): every environment and Ansible variable
- [Evals](reference/evals.md): the golden set, metrics, model comparison
- [Glossary](reference/glossary.md)

## Decisions

Short records of each decision: the context, what was decided, and the
consequences. Add one whenever you make a choice someone might later ask about.

| # | Decision |
|---|---|
| [0001](decisions/0001-guardrails-in-code.md) | Guardrails live in code; the model only classifies and phrases |
| [0002](decisions/0002-local-model-mistral-nemo.md) | Run a local model; use mistral-nemo (with the comparison results) |
| [0003](decisions/0003-postgres-over-mongo.md) | Postgres, not Mongo, for conversation state |
| [0004](decisions/0004-per-customer-advisory-lock.md) | One advisory lock per customer, held through the model call |
| [0005](decisions/0005-no-customer-text-stored.md) | Never store what the customer wrote |
| [0006](decisions/0006-kafka-later.md) | Kafka moves messages later; it doesn't store state |

## Roadmap

[roadmap.md](roadmap.md): the production-readiness list with its status, the
next phases, and decisions still open.
