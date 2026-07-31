"""Day 2 gate: are players actually isolated from each other?

Hearsay's premise is that no player can see what another player really wrote.
That is not something the game enforces — it is a property of how the gateway
buckets messages into conversations. If two players end up sharing one
conversation, they read each other's raw statements and the game is over before
it starts.

The docs do not say whether a Discord DM gets its own conversation or whether
everything in a server lands in one. So we measure instead of assuming.

The test that matters is the last one: **does any conversation contain messages
from more than one distinct sender?** A conversation with two senders is a shared
room, and a shared room cannot hold two seats.

    python spike/isolation.py
    python spike/isolation.py --connection conn_xxx
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from caspian_sdk import CommClient, CommError  # noqa: E402

from spike.probe import _load_dotenv  # noqa: E402


def sender_key(sender: dict | None) -> str:
    """A stable identity for whoever sent a message, whatever the channel calls it."""
    if not sender:
        return "<unknown>"
    for field in ("address", "id", "handle", "username", "name", "email"):
        if sender.get(field):
            return str(sender[field])
    return repr(sorted(sender.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", help="only inspect this connection id")
    args = parser.parse_args()

    _load_dotenv()
    if not os.environ.get("CASPIAN_API_KEY"):
        print("CASPIAN_API_KEY is not set.", file=sys.stderr)
        return 2

    client = CommClient()

    try:
        conversations = client.list_conversations(args.connection)
    except CommError as exc:
        print(f"list_conversations failed ({exc.status_code}): {exc.detail}", file=sys.stderr)
        return 1

    if not conversations:
        print("No conversations yet.\n")
        print("Have two different people message the agent, then re-run this.")
        print("  email:   send to hearsay@agents.trycaspianai.com from two addresses")
        print("  discord: two people DM the bot (or post in a server it joined)")
        return 0

    print(f"{len(conversations)} conversation(s)\n")

    shared: list[tuple[str, set[str]]] = []
    per_channel: dict[str, int] = defaultdict(int)

    for conversation in conversations:
        conversation_id = conversation.get("id")

        try:
            messages = client.list_messages(conversation_id)
        except CommError as exc:
            print(f"  {conversation_id}  <could not read messages: {exc.detail}>")
            continue

        # A conversation record carries no channel — only its messages do.
        channel = next((m.get("channel") for m in messages if m.get("channel")), "?")
        per_channel[channel] += 1

        # chat_type tells us whether the platform considers this a DM or a group.
        chat_types = {m.get("chat_type") for m in messages if m.get("chat_type")}

        # Bounces and autoresponders are not players; counting them as senders
        # would report a false isolation failure.
        senders = {
            sender_key(m.get("sender"))
            for m in messages
            if (m.get("direction") or "inbound") == "inbound" and not m.get("auto_generated")
        }
        senders.discard("<unknown>")
        robots = sum(1 for m in messages if m.get("auto_generated"))

        flag = "  <-- SHARED" if len(senders) > 1 else ""
        extra = f" chat_type={sorted(chat_types)}" if chat_types else ""
        extra += f" auto={robots}" if robots else ""
        print(f"  {conversation_id}  [{channel}]  {len(messages):>3} msgs  "
              f"senders={sorted(senders) or ['none']}{extra}{flag}")

        if len(senders) > 1:
            shared.append((conversation_id, senders))

    print("\nconversations per channel:",
          ", ".join(f"{c}={n}" for c, n in sorted(per_channel.items())))

    if shared:
        print(f"\nISOLATION FAILED: {len(shared)} conversation(s) carry more than one sender.")
        for conversation_id, senders in shared:
            print(f"  {conversation_id}: {sorted(senders)}")
        print("\nTwo players in one conversation can read each other's raw statements.")
        print("Fix: give each player their own conversation (DM, private channel, or a")
        print("different channel entirely) — or seat only one player per shared room.")
        return 1

    print("\nISOLATION HOLDS: every conversation has at most one sender.")
    print("One conversation is one seat. The relay is the only path between players.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
