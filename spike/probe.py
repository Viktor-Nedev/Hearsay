"""Print the live capability matrix for the Caspian gateway.

Hearsay's whole design rests on two capabilities:

  send      - push into an existing conversation without being asked (the relay)
  initiate  - open a conversation with someone who has never written to us (invites)

Both are declared per channel by the gateway, and the declaration does not match
the SDK docs (see FIELDNOTES.md). So we never assume: we ask, and the transport
layer reads the answer. Re-run this whenever a channel starts misbehaving.

    python spike/probe.py
    python spike/probe.py --json > docs/capabilities.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Capabilities Hearsay actually depends on, in the order we care about them.
WANTED = ["receive", "reply", "send", "initiate", "interactions", "reactions", "media"]

BASE_URL = os.environ.get("CASPIAN_BASE_URL", "https://api.trycaspianai.com")


def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env reader so the probe runs without installing anything."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    except FileNotFoundError:
        pass


def fetch_channels(api_key: str) -> list[dict]:
    request = urllib.request.Request(
        f"{BASE_URL}/v1/channels",
        headers={
            "Authorization": f"Bearer {api_key}",
            # Cloudflare in front of the gateway 403s the default urllib
            # User-Agent with error 1010. Any real-looking UA gets through.
            "User-Agent": "hearsay-probe/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def render_table(channels: list[dict]) -> str:
    lines = []
    header = f"{'CHANNEL':<12} {'PROVIDER':<15}" + " ".join(f"{c[:5]:<6}" for c in WANTED)
    lines.append(header)
    lines.append("-" * len(header))

    for entry in channels:
        caps = set(entry.get("capabilities") or [])
        marks = " ".join(f"{'  YES' if c in caps else '   .':<6}" for c in WANTED)
        lines.append(f"{entry.get('channel', '?'):<12} {entry.get('provider', '?'):<15}{marks}")

    lines.append("")
    lines.append(f"legend: {' '.join(f'{c[:5]}={c}' for c in WANTED)}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="dump raw JSON instead of a table")
    args = parser.parse_args()

    _load_dotenv()
    api_key = os.environ.get("CASPIAN_API_KEY")
    if not api_key:
        print("CASPIAN_API_KEY is not set. Copy .env.example to .env first.", file=sys.stderr)
        return 2

    try:
        channels = fetch_channels(api_key)
    except urllib.error.HTTPError as exc:
        print(f"gateway returned {exc.code}: {exc.read().decode(errors='replace')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"could not reach {BASE_URL}: {exc.reason}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(channels, indent=2))
    else:
        print(render_table(channels))

    # The gate: Hearsay needs `send` on at least two channels to relay at all.
    relayable = [c["channel"] for c in channels if "send" in (c.get("capabilities") or [])]
    print(f"\nrelay-capable channels ({len(relayable)}): {', '.join(relayable) or 'none'}")
    if len(relayable) < 2:
        print("FAIL: need `send` on two channels or the relay cannot work.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
