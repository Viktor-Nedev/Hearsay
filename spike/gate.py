"""Day 1 gate: prove the relay primitive actually works, not just that it is declared.

Hearsay lives or dies on one call: pushing a message into a conversation that the
agent was not asked to reply to. `client.channels()` says email supports `send`,
but a declaration is not a delivery. This drives a real inbound email, waits for
the gateway to open a conversation, then proactively sends into it and confirms
the gateway reports it as sent.

    python spike/gate.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from caspian_sdk import CommClient, CommError  # noqa: E402

from spike.probe import _load_dotenv  # noqa: E402

POLL_SECONDS = 2
TIMEOUT_SECONDS = 90


def wait_for_event(client: CommClient, event_type: str, after_seq: int, timeout: int) -> dict | None:
    """Poll the ordered event stream until one event of `event_type` shows up."""
    deadline = time.time() + timeout
    cursor = after_seq
    while time.time() < deadline:
        for event in client.events(after_seq=cursor, limit=100):
            cursor = max(cursor, event.get("seq", cursor))
            if event.get("type") == event_type:
                return event
        time.sleep(POLL_SECONDS)
    return None


def main() -> int:
    _load_dotenv()
    if not os.environ.get("CASPIAN_API_KEY"):
        print("CASPIAN_API_KEY is not set. Copy .env.example to .env first.", file=sys.stderr)
        return 2

    client = CommClient()
    username = os.environ.get("HEARSAY_EMAIL_USERNAME", "hearsay")

    # 1. Connect email. Idempotent: a second run returns the same connection.
    try:
        inbox = client.connect_email(username=username)
    except CommError as exc:
        print(f"connect_email failed ({exc.status_code}): {exc.detail}", file=sys.stderr)
        return 1
    print(f"[1/4] inbox        {inbox.get('address')}  (connection {inbox.get('id')})")

    # Everything after this point must only see events we caused.
    baseline = client.events(limit=1)
    cursor = baseline[-1]["seq"] if baseline else 0

    # 2. Drive an inbound email so a conversation exists to push into.
    client.test_email(text="gate: opening a conversation", subject="hearsay gate")
    print("[2/4] inbound       sent test email, waiting for message.received...")

    received = wait_for_event(client, "message.received", cursor, TIMEOUT_SECONDS)
    if not received:
        print(f"FAIL: no message.received within {TIMEOUT_SECONDS}s", file=sys.stderr)
        return 1

    message = (received.get("data") or {}).get("message") or {}
    conversation_id = message.get("conversation_id")
    print(f"[3/4] conversation  {conversation_id}")

    # 3. The actual gate: proactive send. Not message.reply() — an unprompted push.
    cursor = received["seq"]
    try:
        sent = client.send_message(
            conversation_id,
            text="gate: this was pushed proactively, not replied. The relay works.",
        )
    except CommError as exc:
        print(f"FAIL: send_message rejected ({exc.status_code}): {exc.detail}", file=sys.stderr)
        return 1

    confirmed = wait_for_event(client, "message.sent", cursor, TIMEOUT_SECONDS)
    status = "sent" if confirmed else "queued (no message.sent event yet)"
    print(f"[4/4] proactive     message {sent.get('id')} -> {status}")

    if not confirmed:
        print(f"\nWARN: send accepted but not confirmed within {TIMEOUT_SECONDS}s.", file=sys.stderr)
        return 1

    print("\nGATE PASSED: proactive send_message() delivers on email.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
