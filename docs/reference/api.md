# HTTP API

[`care/api.py`](../../care/api.py). This is what the WhatsApp integration and
the agent desk call. Start it with
`CARE_API_TOKEN=... CARE_DATABASE_URL=... uvicorn care.api:create_app --factory`.
FastAPI also serves interactive docs at `/docs`.

**Auth:** every route except `/health` needs `Authorization: Bearer
$CARE_API_TOKEN`, and returns **401** otherwise.

**Trust:** `msisdn` and `area` are trusted, so they must come from the
WhatsApp layer, never from message text. `msisdn` is 9–15 digits, in bodies
and paths; anything else gets **422**.

## Endpoints

| Endpoint | Body | Returns | Errors |
|---|---|---|---|
| `GET /health` | — | `{"ok": true}` (no auth) | |
| `POST /messages` | `{message_id, msisdn, area, text}` | `MessageResult` | 422 bad input |
| `GET /conversations/{msisdn}` | — | the open conversation, or the latest one, with `messages` (last 50) and `events` | 404 new customer |
| `GET /customers/{msisdn}/conversations` | — | the last 20 conversations, newest first | |
| `POST /conversations/{msisdn}/pickup` | `{agent_id}` | the conversation | 404 none open; 409 the bot owns it |
| `POST /conversations/{msisdn}/release` | — | the conversation, back with the bot and still open | 404 none open; 409 the bot already owns it |
| `POST /conversations/{msisdn}/close` | `{reason: "resolved" \| "agent_resolved"}` | the closed conversation | 404 none open; 422 other reasons |

## `POST /messages`

Request limits: `message_id` 1–128 characters, `area` 1–64, `text` 1–4096
(WhatsApp's own limit). Bad input gets 422 before any model call.

```json
{"message_id": "wamid.1", "conversation_id": "d26e8186-…", "status": "bot",
 "reply": "Your balance: $3.20 airtime and 850 MB data.", "route": "balance",
 "handoff": false, "blocked": [], "duplicate": false}
```

| Field | Meaning |
|---|---|
| `status` | Who owns the conversation after this message: `bot` or `agent`. |
| `reply` | Text to send the customer, or `null` when an agent owns the conversation (forward the message to them). |
| `route`, `handoff`, `blocked` | The decision. See [reply outcomes](architecture.md#7-reply-outcomes); `route` is `agent` when the bot was skipped. |
| `duplicate` | `true` if this `message_id` was handled before; the stored result is returned. |

What happens, in order, all in one transaction (`Store.turn`):

1. If the `message_id` was seen before, return the stored outcome with
   `duplicate: true`. WhatsApp redelivers messages, and this stops them being
   handled twice. A redelivery never opens a conversation.
2. Join the customer's open conversation. If there's none, or it has been idle
   past the window, close the old one and open a new one.
3. If an agent owns the conversation, record the message and return
   `reply: null, route: "agent"`.
4. Otherwise run `handle()` and record the intent, confidence and latency. If
   the reply is a handoff, the conversation moves to `agent`, with the route
   and blocked reasons as the reason.

The locking that makes this safe with any number of workers is described in
[data model → concurrency](data-model.md#concurrency).
