"""Five minutes before recording: is everything actually up?

    python spike/preflight.py

Green or red, once, against the live gateway. It sends real messages — you will
see two arrive, one in Discord and one in the inbox — because "the connection
says active" has been wrong before and a declaration is not a delivery.

It also reports the Gemini quota, which is the thing most likely to be quietly
spent when you need it: the free tier allows roughly eight rewrites a day, and
once they are gone the game keeps running but tampering degrades to the offline
backend, which reads visibly cruder on camera.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from caspian_sdk import CommClient, CommError  # noqa: E402

from spike.probe import _load_dotenv  # noqa: E402

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)

WAIT_SECONDS = 45


def ok(label: str, detail: str = "") -> bool:
    print(f"  {GREEN}OK  {RESET} {label:<34} {DIM}{detail}{RESET}")
    return True


def bad(label: str, detail: str = "") -> bool:
    print(f"  {RED}FAIL{RESET} {label:<34} {detail}")
    return False


def warn(label: str, detail: str = "") -> None:
    print(f"  {YELLOW}WARN{RESET} {label:<34} {detail}")


def check_connections(client) -> tuple[bool, dict]:
    try:
        connections = client._request("GET", "/v1/connections")
    except CommError as exc:
        return bad("gateway reachable", f"{exc.status_code}: {exc.detail}"), {}

    live = {}
    healthy = True
    for connection in connections:
        channel, status = connection.get("channel"), connection.get("status")
        if status == "active":
            live[channel] = connection
            ok(f"{channel} connected", connection.get("address") or "")
        else:
            healthy = bad(f"{channel} connected", f"status is {status}")

    for required in ("email", "discord"):
        if required not in live:
            healthy = bad(f"{required} present", "the two-channel rule needs it")
    return healthy, live


def check_delivery(client, live: dict) -> bool:
    """Actually send. A capability is a claim; message.sent is evidence."""
    healthy = True
    baseline = client.events(limit=1)
    cursor = baseline[-1]["seq"] if baseline else 0

    targets = {}
    for conversation in client.list_conversations():
        try:
            messages = client.list_messages(conversation["id"])
        except CommError:
            continue
        channel = next((m.get("channel") for m in messages if m.get("channel")), None)
        if channel in live and channel not in targets:
            targets[channel] = conversation["id"]

    for channel in ("discord", "email"):
        conversation_id = targets.get(channel)
        if conversation_id is None:
            warn(f"{channel} delivery", "no conversation yet — write to the agent once")
            continue
        try:
            client.send_message(conversation_id, text="Preflight check. Ignore this.")
        except CommError as exc:
            healthy = bad(f"{channel} delivery", f"{exc.status_code}: {exc.detail}")
            continue

        deadline = time.time() + WAIT_SECONDS
        confirmed = False
        while time.time() < deadline and not confirmed:
            for event in client.events(after_seq=cursor, limit=100):
                cursor = max(cursor, event["seq"])
                message = (event.get("data") or {}).get("message") or {}
                if event["type"] == "message.sent" and message.get("channel") == channel:
                    confirmed = True
                    break
            if not confirmed:
                time.sleep(2)

        if confirmed:
            ok(f"{channel} delivery", "message.sent confirmed")
        else:
            healthy = bad(f"{channel} delivery", f"accepted but unconfirmed in {WAIT_SECONDS}s")
    return healthy


def check_rewriter() -> bool:
    from hearsay.tamper import build_rewriter

    rewriter = build_rewriter()
    name = getattr(rewriter, "name", "?")
    if name != "gemini":
        warn("rewriter", f"running on {name} — tampering will read cruder")
        return True

    original = "i was asleep the whole time"
    result = rewriter.rewrite(original, "Ochre", "they saw Jade leaving")
    if result == original:
        warn("gemini quota", "spent — tampering falls back to the offline backend")
    elif getattr(rewriter, "_cooldown_until", 0) > time.time():
        warn("gemini quota", "hit its limit on this call; the fallback is in use")
    else:
        ok("gemini", f"{rewriter.model} -> {result[:38]!r}")
    return True


def main() -> int:
    _load_dotenv()
    if not os.environ.get("CASPIAN_API_KEY"):
        print("CASPIAN_API_KEY is not set.", file=sys.stderr)
        return 2

    print(f"\n{BOLD}preflight{RESET}\n")
    client = CommClient()

    healthy, live = check_connections(client)
    if live:
        healthy &= check_delivery(client, live)
    healthy &= check_rewriter()

    print()
    if healthy:
        print(f"{GREEN}{BOLD}ready to record.{RESET}")
        print(f"{DIM}Two test messages were just sent. Ignore them, or delete the "
              f"Discord one before you start.{RESET}\n")
        return 0

    print(f"{RED}{BOLD}not ready.{RESET} Fix the red lines above before recording.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
