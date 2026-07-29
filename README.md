# Hearsay

**A social deduction game where the messenger is the impostor.**

Five players. Nobody shares a channel. One is on Discord, one is on Slack, one is
reading email. They have never exchanged contact details and they cannot see each
other. Every word that travels between them is carried by an agent.

One player is the **Impostor**. Their power is not to lie — it is to change what
somebody *else* said, in transit, in that person's own voice.

So you can be voted out for a sentence you never wrote. And you cannot prove you
didn't write it, because the only witness to what you actually said is the thing
that changed it.

> Every agent in this hackathon talks to you. This one talks *between* people —
> and it does not always tell the truth.

Built for the [Caspian Buildathon](https://caspian.devpost.com/) on
[`caspian-sdk`](https://github.com/TryCaspian/caspian-sdk).

---

## Why this needs Caspian specifically

Most agents use multi-channel as a convenience: reach me wherever I am. Hearsay
uses it as a **game mechanic**.

The isolation between players *is* the channel boundary. A Discord player cannot
scroll up and check what the email player really wrote, because they are not in
the same room and never were — there is no shared room anywhere in the system.
The agent is the only path between them, which is precisely what makes tampering
undetectable from the inside.

That has a sharp consequence: **duplicating the handler per channel would destroy
the game.** A per-channel handler cannot relay Discord into email, cannot hold a
single game state across four platforms, and cannot rewrite one player's words on
the way to another. The single-handler rule isn't a box we tick — it is load-bearing.

## Status

Day 1 of 15. This README documents what runs today, not what is planned.

- [x] Live capability matrix probed and recorded — [`docs/capabilities.md`](docs/capabilities.md)
- [x] Relay primitive proven end-to-end on email — [`spike/gate.py`](spike/gate.py)
- [ ] Game engine
- [ ] Tamper rewriter
- [ ] Multi-channel play
- [ ] Demo video

## Verified so far

The agent is live at `hearsay@agents.trycaspianai.com`.

```
$ python spike/gate.py
[1/4] inbox        hearsay@agents.trycaspianai.com  (connection conn_25a3…)
[2/4] inbound       sent test email, waiting for message.received...
[3/4] conversation  conv_7bdae37f938ee29521cadf3b
[4/4] proactive     message msg_3f29750440d649fe2a98ee0b -> sent

GATE PASSED: proactive send_message() delivers on email.
```

That is the whole game in miniature: a message pushed into a conversation that
nobody asked us to reply to.

## Setup

```bash
git clone <this repo> && cd hearsay
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -e ".[dev]"

cp .env.example .env
# Get a sandbox key — no signup, no card:
curl -s -X POST https://api.trycaspianai.com/v1/projects/sandbox \
  -H 'Content-Type: application/json' -d '{"name":"hearsay"}'
# Paste api_key into .env, then:

python spike/probe.py     # what can your gateway do?
python spike/gate.py      # can it actually relay?
```

## Field notes

The brief asked builders to run the thing for real and say where it hurt.
[`FIELDNOTES.md`](FIELDNOTES.md) is that, written as we go — including the point
on Day 1 where the SDK docs and the live gateway disagreed about which channels
can cold-start a conversation, and why we now probe instead of trust.

## License

MIT.
