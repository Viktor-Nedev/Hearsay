# Live channel capabilities

Probed from the hosted gateway (`https://api.trycaspianai.com/v1/channels`) on
**29 July 2026** with `caspian-sdk` 0.6.1. Regenerate with:

```bash
python spike/probe.py                     # table
python spike/probe.py --json              # raw
```

Raw captures live alongside this file: [capabilities.txt](capabilities.txt),
[capabilities.json](capabilities.json).

```
CHANNEL      PROVIDER       recei  reply  send   initi  inter  react  media
---------------------------------------------------------------------------
email        ses              YES    YES    YES    YES     .      .     YES
discord      discord          YES    YES    YES    YES    YES    YES    YES
slack        slack            YES    YES    YES     .     YES    YES    YES
x            x                YES    YES    YES     .      .      .      .
telegram     telegram         YES    YES    YES     .     YES    YES    YES
phone        twilio           YES    YES    YES    YES     .      .      .
phone        telnyx           YES    YES    YES    YES     .      .      .
bluesky      bluesky          YES    YES    YES     .      .      .      .
gmeet        caspian-gmeet    YES     .      .     YES     .      .      .
```

## What Hearsay needs, and where it comes from

| Capability | Why Hearsay needs it | Channels that have it |
|---|---|---|
| `send` | **The relay.** Push a round transcript into a conversation nobody asked us to reply to. Without this there is no game. | email, discord, slack, telegram, x, phone, bluesky |
| `receive` | Collect statements and votes. | all |
| `initiate` | **Invites.** Cold-open a thread with a player who has never written to us. | email, discord, phone, gmeet |
| `interactions` | Vote by button instead of typing. | discord, slack, telegram |
| `reactions` | Lightweight acks ("your statement landed"). | discord, slack, telegram |

## Chosen channel mix

**Discord + Email + Slack.** All free, all `send`-capable, and they span the
interesting axis: Discord and Slack are rich and instant (buttons, reactions),
email is plain and slow. A game that stays coherent across that gap is the whole
point — see [FIELDNOTES.md](../FIELDNOTES.md).

`phone` (Twilio) stays optional. It is the only channel with both `initiate` and
a hard 160-character limit, which makes it the most interesting stress test for
the renderer, but it costs money and needs A2P registration.

## Verified end-to-end, not just declared

A declaration is not a delivery. [`spike/gate.py`](../spike/gate.py) drives a real
inbound email, waits for the gateway to open a conversation, pushes into it with
`send_message()`, and confirms the gateway emits `message.sent`:

```
[1/4] inbox        hearsay@agents.trycaspianai.com  (connection conn_25a3…)
[2/4] inbound       sent test email, waiting for message.received...
[3/4] conversation  conv_7bdae37f938ee29521cadf3b
[4/4] proactive     message msg_3f29750440d649fe2a98ee0b -> sent

GATE PASSED: proactive send_message() delivers on email.
```
