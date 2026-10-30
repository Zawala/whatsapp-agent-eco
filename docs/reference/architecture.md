# Architecture

How a WhatsApp message turns into a reply, and where each guardrail sits.
For a module-by-module reference see [components.md](components.md); for the
conversation state, see [data-model.md](data-model.md).

> Diagrams are [Mermaid](https://mermaid.js.org/). GitHub renders them inline;
> in an editor, use a Mermaid preview extension.

## 1. System context

What runs where. Everything is local: no cloud LLM, no real backends.

```mermaid
flowchart LR
    user([Customer on WhatsApp])
    agent([Human care agent])

    subgraph local["Local machine"]
        demo["demo.py / scripts/simulate.sh<br/>(stand in for the WhatsApp channel)"]
        api["care/api.py<br/>FastAPI · POST /messages"]
        pg[("Postgres<br/>conversations · messages")]
        bot["care package<br/>assistant.handle()"]
        fakes[("Fake backends<br/>balances · outages · tickets")]
        kb[("knowledge.py<br/>bundles + FAQs")]
        ollama["Ollama<br/>mistral-nemo"]
        phoenix["Phoenix<br/>(optional tracing)"]
    end

    user -- message --> demo
    demo -- "HTTP: message_id, msisdn, area, text" --> api
    api -- "lock customer, claim message,<br/>owner (bot / agent)" --> pg
    api -- "text + Session<br/>(only if the bot owns it)" --> bot
    bot -- "classify / answer<br/>(OpenAI-compatible API)" --> ollama
    bot -- tool calls --> fakes
    bot -- retrieval --> kb
    bot -. "spans (decisions only)" .-> phoenix
    bot -- Reply --> api
    api -- "reply, or null if an agent owns it" --> demo
    demo -- reply text --> user
    api -. "handoff: conversation -> agent" .-> agent
    agent -. "pickup / release / close" .-> api
    sweep["care/sweep.py<br/>(timer)"] -- "close idle" --> pg
```

There is no WhatsApp integration yet. `scripts/simulate.sh` (over the API) and
`demo.py` (calling `handle()` directly, with no state) play the channel's role.
They supply the phone number, area and message ID that a real webhook would
fill from WhatsApp metadata.

A handoff moves the conversation to `agent` in Postgres, and the bot stays
quiet until an agent releases or closes it (see [§10](#10-conversation-lifecycle)). The agent desk itself
isn't built: the endpoints exist, but nothing notifies an agent yet.

## 2. Message pipeline

`care/assistant.py: handle()` is the single entry point. Every path ends in
either an answer or a handoff. Nothing returns an error to the customer.

```mermaid
flowchart TD
    start(["handle(text, session)"]) --> redactIn["Redact PII from input<br/><code>guard.redact(INPUT_ENTITIES)</code>"]
    redactIn --> hr{"High-risk regex?<br/><code>router.is_high_risk</code>"}
    hr -- yes --> H1["Handoff<br/>route=high_risk"]
    hr -- no --> cls["LLM intent classifier<br/><code>router.classify</code><br/>JSON, 1 retry, then fallback"]
    cls --> gate{"confidence ≥ 0.6<br/>and intent not<br/>unknown / other?"}
    gate -- no --> H2["Handoff<br/>route=&lt;intent&gt;"]
    gate -- yes --> sw{intent}

    sw -- balance --> tb["call_tool get_balance"]
    sw -- outage --> to["call_tool get_outage"]
    sw -- fault_report --> tf["call_tool create_ticket"]
    sw -- bundle_info --> ret["Keyword retrieval<br/><code>retrieval.retrieve</code>"]

    tb & to & tf --> tok{"tool ok?"}
    tok -- no --> H3["Handoff<br/>blocked=tool:&lt;error&gt;"]
    tok -- yes --> tmpl["Fixed reply template"]

    ret --> docs{"any docs?"}
    docs -- no --> H4["Handoff<br/>blocked=no_context"]
    docs -- yes --> gen["LLM answers from context"]
    gen --> chk{"Output checks<br/><code>checks.run_all</code><br/>prices · policy · canary"}
    chk -- violation --> H5["Handoff<br/>blocked=&lt;violations&gt;"]
    chk -- clean --> ans["LLM answer"]

    tmpl & ans & H1 & H2 & H3 & H4 & H5 --> redactOut["Redact PII from output<br/><code>guard.redact(OUTPUT_ENTITIES)</code>"]
    redactOut --> done(["Reply(text, route, handoff, blocked)"])

    exc["Any exception<br/>in the pipeline"] -.-> H6["Handoff<br/>route=error"]
    H6 --> redactOut

    classDef handoff fill:#fde2e1,stroke:#c0392b,color:#000
    classDef llm fill:#e3ecfb,stroke:#2f5fb3,color:#000
    class H1,H2,H3,H4,H5,H6 handoff
    class cls,gen llm
```

Blue boxes are the only two places the model is used. Red boxes are handoffs.

Points the diagram makes explicit:

- **High-risk messages make zero LLM calls.** The regex runs before the classifier.
- **The classifier and retrieval both see redacted text**, never the raw message.
- **Output checks only apply to LLM-written text** (`bundle_info`). Balance,
  outage and fault replies are fixed templates filled from tool results, so
  there's nothing for the model to get wrong.
- **Output redaction runs on every reply**, handoffs included.
- **`language` is recorded but not used for routing yet.**

## 3. Sequence: a bundle question

The most involved path, and the only one with two model calls.

```mermaid
sequenceDiagram
    autonumber
    participant D as demo.py
    participant A as assistant
    participant G as guard
    participant R as router
    participant L as llm (Ollama)
    participant K as retrieval + knowledge
    participant C as checks

    D->>A: handle("How much is the 5GB bundle?", session)
    A->>G: redact(text, INPUT_ENTITIES)
    G-->>A: clean text
    A->>R: is_high_risk(clean)
    R-->>A: False
    A->>R: classify(clean, llm)
    R->>L: system prompt + <<<message>>> (json_mode)
    L-->>R: {"intent":"bundle_info","language":"en","confidence":0.95}
    R->>R: validate() schema
    R-->>A: route
    A->>K: retrieve(clean)
    K->>K: current_docs(today) drops expired docs
    K->>K: score, keep docs ≥ 80% of best, top 3
    K-->>A: [data-5gb]
    A->>L: SYSTEM (with canary) + context + <<<question>>>
    L-->>A: "The 5GB Weekly bundle costs $4.50…"
    A->>C: run_all(answer, allowed={4.50})
    C-->>A: [] (no violations)
    A->>G: redact(answer, OUTPUT_ENTITIES)
    A-->>D: Reply(route=bundle_info, handoff=False)
```

If the model had said `$4.00` (the superseded price), `check_prices` would
return `price_not_in_context:4.0` and the customer would get the handoff text.

## 4. Sequence: a tool route (fault report)

Tool routes never let the model touch identity or free text.

```mermaid
sequenceDiagram
    autonumber
    participant D as demo.py
    participant A as assistant
    participant R as router
    participant T as tools.call_tool
    participant B as fake backend

    D->>A: handle("No signal since morning", session[msisdn, area, message_id])
    A->>R: is_high_risk → False, classify → fault_report (0.9)
    A->>T: call_tool("create_ticket", {category, summary=fixed label}, session)
    T->>T: tool in allowlist? args ⊆ model_args? approval needed?
    T->>B: _create_ticket(session, args)
    B->>B: ticket keyed by session.message_id (idempotent)
    B-->>T: {"ok": true, "ticket_id": "TKT-00001"}
    T-->>A: result
    A-->>D: "Thanks, we've logged this as ticket TKT-00001…"
```

A WhatsApp retry of the same message carries the same `message_id`, so it gets
the same ticket instead of a duplicate.

## 5. Classifier contract

```mermaid
stateDiagram-v2
    [*] --> Attempt1
    Attempt1 --> Valid: validate() ok
    Attempt1 --> Attempt2: ValueError<br/>(feed reason back to model)
    Attempt2 --> Valid: validate() ok
    Attempt2 --> Fallback: ValueError
    Valid --> [*]: {intent, language, confidence}
    Fallback --> [*]: intent=unknown, confidence=0.0
```

`validate()` rejects: non-JSON, non-object, intent outside
`{bundle_info, balance, outage, fault_report, other}`, language outside
`{en, sn, nd, other}`, confidence that isn't a number in `[0, 1]` (booleans
rejected). The fallback always ends in a handoff.

## 6. Tool gate

```mermaid
flowchart LR
    call["call_tool(name, model_args, session)"] --> a{"name in TOOLS?"}
    a -- no --> e1["error: unknown_tool"]
    a -- yes --> b{"model_args ⊆<br/>tool.model_args?"}
    b -- no --> e2["error: bad_args"]
    b -- yes --> c{"needs_approval and<br/>no session.approved_by?"}
    c -- yes --> e3["error: approval_required"]
    c -- no --> d["tool.fn(session, args)"]
    d -- raises --> e4["error: backend_error"]
    d -- returns --> ok["{ok: true, ...}"]
```

Identity (`msisdn`), `area` and the idempotency key (`message_id`) come from
the `Session`. The model can't pass them because no tool declares them in
`model_args`.

## 7. Reply outcomes

Every reply is a `Reply(text, route, handoff, blocked)`. This table lists
every way one can be produced.

| Situation | `route` | `handoff` | `blocked` |
|---|---|---|---|
| Balance found | `balance` | False | `[]` |
| Outage lookup (with or without an outage) | `outage` | False | `[]` |
| Ticket created | `fault_report` | False | `[]` |
| Bundle answer passed all checks | `bundle_info` | False | `[]` |
| High-risk regex matched | `high_risk` | True | `[]` |
| Classifier said `other` | `other` | True | `[]` |
| Classifier failed twice | `unknown` | True | `[]` |
| Confidence < 0.6 | the intent, e.g. `balance` | True | `[]` |
| Tool returned an error | the intent | True | `["tool:not_found"]` etc. |
| Retrieval found nothing | `bundle_info` | True | `["no_context"]` |
| Answer failed output checks | `bundle_info` | True | e.g. `["price_not_in_context:4.0", "canary_leak"]`, or `["price_unparsed:four five dollars"]` |
| Any exception | `error` | True | `["exception:<Type>"]` |

## 8. Observability

```mermaid
flowchart LR
    h["assistant.handle"] -- "span 'handle'<br/>intent, language, confidence,<br/>retrieval.doc_ids, route,<br/>handoff, blocked, exceptions" --> otel["OpenTelemetry"]
    llm["llm.chat (OpenAI client)"] -- "auto-instrumented LLM spans<br/>inputs/outputs hidden" --> otel
    otel -- "only if tracing.enable()" --> px["Phoenix :6006"]
```

Without `tracing.enable()` (i.e. without `demo.py --trace`), the OpenTelemetry
calls are no-ops. Spans never contain message text or model output.

## 9. Testing and evals

```mermaid
flowchart LR
    subgraph offline["pytest, no model"]
        fake["FakeLLM<br/>scripted replies, counts calls"] --> core["tests/test_core.py<br/>every guardrail + pipeline path"]
        fake --> tapi["tests/test_api.py<br/>API, state, several workers"]
        tapi --> tpg[("Postgres<br/>CARE_TEST_DATABASE_URL")]
    end
    subgraph live["needs Ollama"]
        tl["tests/test_live.py<br/>prompt injection ×3, no canary leak"]
        ev["evals/run.py<br/>classifier vs golden.jsonl<br/>--min-routed-accuracy gate"]
        cmp["evals/compare.py<br/>every model: speed vs accuracy"]
    end
    golden[("evals/golden.jsonl")] --> ev
    golden --> core
    cmp --> ev
    ev --> res[("evals/results/*.json")]
    res -. "--baseline" .-> ev
```

CI runs these automatically:

```mermaid
flowchart LR
    push(["push / PR"]) --> ci["ci.yml · GitHub-hosted<br/>test_core + test_api (Postgres service)<br/>playbook syntax + ansible-lint<br/>compose config"]
    push -- "model-related paths,<br/>nightly, manual" --> evj
    subgraph evj["evals.yml · self-hosted, label ollama"]
        direction LR
        s["start.yml"] --> g["evals.run<br/>≥ 0.95, no high-risk miss"] --> l["test_live.py"] --> sim["simulate.sh"] --> st["stop.yml<br/>(always)"]
    end
```

`test_core.py` also checks the high-risk regex against the `high_risk` labels in
`golden.jsonl`, so the regex and the eval labels can't drift apart.

## 10. Conversation lifecycle

Over the API, messages from one customer form a conversation that opens,
continues within a window (24 h by default), and closes as `timeout`,
`abandoned`, `resolved` or `agent_resolved`. The state diagram, close
reasons, tables and locking are in [data model](data-model.md#lifecycle).
