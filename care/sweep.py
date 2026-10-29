"""Close conversations idle past the window, so metrics don't wait for the customer's next message.

    CARE_DATABASE_URL=... python -m care.sweep

Run on a timer (deploy/ansible installs one). Correctness doesn't depend on it:
a customer's next message closes a stale conversation itself.
"""
from .state import store_from_env

if __name__ == "__main__":
    store = store_from_env()
    try:
        print(f"closed {store.close_idle()} idle conversation(s)")
    finally:
        store.close()
