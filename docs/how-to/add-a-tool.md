# Add a tool

A tool is the only way the bot touches a backend. The gate in
[`tools.call_tool`](../../care/tools.py) is the safety boundary, so every
rule below is there to keep it intact.

## Steps

1. **Write the backend function** in [`care/tools.py`](../../care/tools.py):
   `def _my_tool(s: Session, args: dict) -> dict`.
   - Return `{"ok": True, ...}` or `{"ok": False, "error": "<code>"}`. If it
     raises anyway, `call_tool` turns that into `backend_error`.
   - **Take identity from the session, never from `args`:** `s.msisdn`,
     `s.area`.
   - **If it creates something** (a ticket, an order), key it on
     `s.message_id` so a WhatsApp redelivery doesn't create a second one. See
     `_create_ticket`.
2. **Register it in `TOOLS`:**
   `Tool("my_tool", model_args={...}, needs_approval=..., fn=_my_tool)`.
   - **`model_args`** is the full set of arguments the model may pass. Keep it
     as small as possible, often empty. Anything else is rejected with
     `bad_args`.
   - **`needs_approval=True`** for anything that moves money or changes an
     account. It then only runs if `session.approved_by` is set by a human
     flow, which doesn't exist yet. See `reverse_transaction`.
3. **Call it from a route handler** in
   [`care/assistant.py`](../../care/assistant.py). Hand off on any error,
   otherwise fill a **fixed template**. Don't let the model phrase tool results:
   templates can't make things up. If it serves a new intent, see
   [add an intent](add-an-intent.md).
4. **For a real backend:** put the client behind the same function signature.
   Add timeouts, and map failures to error codes. Never let exceptions or raw
   backend errors reach the customer.

## Tests

In [`tests/test_core.py`](../../tests/test_core.py), next to the existing
tool tests:

- extra arguments are refused: copy `test_model_cannot_choose_whose_balance`;
- if gated, it's refused without approval: copy `test_reversal_needs_approval`;
- if it creates something, the same `message_id` gives the same result: copy
  `test_duplicate_message_id_gives_same_ticket`;
- through the pipeline, the template is used and errors hand off with
  `tool:<error>`.

Update the tool table in
[components](../reference/components.md#caretoolspy--tool-allowlist-and-fake-backends).
