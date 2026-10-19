"""Chat with the assistant by hand. `python demo.py --trace` sends spans to Phoenix."""
import sys
import uuid

from care.assistant import handle
from care.tools import Session

if __name__ == "__main__":
    provider = None
    if "--trace" in sys.argv:
        from care import tracing
        provider = tracing.enable()
    print("Type a message (Ctrl-D to quit).")
    try:
        while True:
            try:
                text = input("> ").strip()
            except EOFError:
                break
            if not text:
                continue
            session = Session(msisdn="263772000001", area="harare-cbd", message_id=f"wamid.{uuid.uuid4().hex}")
            r = handle(text, session)
            print(r.text)
            print(f"  [route={r.route} handoff={r.handoff} blocked={r.blocked}]")
    finally:
        if provider:
            provider.force_flush()
