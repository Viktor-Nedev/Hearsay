# Field notes

> The brief asked us to build the agent we actually want running, run it for real,
> and say where it hurt. This is the "where it hurt" file. It is written as we go,
> not reconstructed afterwards. Nothing here is a complaint — every entry is
> something we hit while building, with what we did about it.

Environment unless stated otherwise: `caspian-sdk` 0.6.1, Python 3.14.5, Windows 11,
hosted gateway at `api.trycaspianai.com`, sandbox project key.

---

## Day 1 — 29 July

### 1. The docs and the live gateway disagree about `initiate`

This one mattered, because we designed around the documented answer and the
documented answer was wrong.

`examples/reminder.py` says `initiate()` "works on any channel with the INITIATE
capability — today that's SMS." `examples/README.md` lists `initiate` under
**SMS**, with "bring-your-own carrier" setup. Reading only that, cold-starting a
conversation looks like it costs a Twilio number and A2P registration.

The repo's own test suite says something stronger — `server/tests/test_capabilities.py`:

```python
# Email declares neither proactive send nor initiate in this slice.
assert "send" not in by_channel["email"]["capabilities"]
assert "initiate" not in by_channel["telegram"]["capabilities"]
```

The live gateway says otherwise:

```
email     receive reply send initiate media
discord   receive reply send initiate interactions reactions media see_bots group_visibility
```

Email and Discord both have `initiate` **and** `send`, for free, with no carrier
and no signup. That is a much better story than the docs tell, and it is the
difference between "players must find the bot themselves" and "the agent can
invite them." We only found it because we probed instead of trusting the README.

The test comment says "in this slice," so it is presumably describing the offline
fake providers rather than production — but a reader has no way to know that, and
the examples README repeats the SMS-only framing without the caveat.

**What we did:** never hardcode a capability. [`spike/probe.py`](spike/probe.py)
dumps the live matrix, [`docs/capabilities.md`](docs/capabilities.md) records it,
and the transport layer branches on what the gateway actually reports.

**Suggested fix:** have the docs read from the same source as `/v1/channels`, or
add one line to `examples/README.md` — *"capabilities vary by gateway; run
`client.channels()` to see yours."*

### 2. A declaration is not a delivery

`/v1/channels` saying `send` only means the adapter claims the capability. Our
entire game is one primitive — push into a conversation nobody asked us to reply
to — so we wrote [`spike/gate.py`](spike/gate.py) to prove it before building on
it: drive a real inbound email, wait for the conversation to open, `send_message()`
into it, and confirm the gateway emits `message.sent`.

It passed first try, in about fifteen seconds. Worth saying plainly: the thing
worked exactly as advertised, and having `test_email()` in the SDK meant we could
prove it without another human in the loop. That is a genuinely good affordance
and we would not have got to a verified relay on day one without it.

### 3. Cloudflare 403s the default Python User-Agent

Writing a dependency-free probe with `urllib.request` gets:

```
gateway returned 403: error code: 1010
```

Cloudflare error 1010 is a browser-integrity block on the default
`Python-urllib/3.x` User-Agent. `curl` to the same URL with the same key works,
which sends you looking at your auth instead of your headers. Setting any
real-looking `User-Agent` fixes it.

Does not affect the SDK itself (it sets its own UA) — it only bites people
writing raw HTTP calls against the gateway, which the `SKILL.md` quickstart
actively encourages with its `curl` snippets.

**Suggested fix:** one line in the REST docs, or let the default UA through.

### 4. `caspian_sdk` has no `__version__`

```python
>>> import caspian_sdk; hasattr(caspian_sdk, "__version__")
False
```

Installed version is 0.6.1 per pip metadata. Minor, but for a library whose
behaviour depends on which gateway build it is talking to, being unable to log
the SDK version from inside the process is a small papercut when something
misbehaves. Worked around with `importlib.metadata.version("caspian-sdk")`.

---

## Day 2 — 30 July

### 5. The SDK drops `auto_generated`, so a handler cannot spot a bounce

This is the one we would most like fixed, and it came from reading our own data
rather than from anything going wrong.

The Day 1 gate left two conversations behind, not one. The second was a bounce
from `MAILER-DAEMON@amazonses.com`, and the gateway had labelled it correctly:

```
inbound  ch=email  auto=False  from=tester@agents.trycaspianai.com
outbound ch=email  auto=False  from=hearsay@agents.trycaspianai.com
inbound  ch=email  auto=True   from=MAILER-DAEMON@amazonses.com
```

`GET /v1/messages` returns `auto_generated` and `chat_type`. The `Message`
dataclass handed to `on_message` carries neither:

```python
>>> [f.name for f in dataclasses.fields(Message)]
['id', 'conversation_id', 'connection_id', 'customer_id', 'agent_id',
 'channel', 'sender', 'subject', 'text', 'html', 'media']
```

So the gateway knows a message is machine-generated, and then the SDK throws that
away one layer before anybody can act on it. For a support agent this produces a
polite reply to a no-reply address. For Hearsay it is worse, because we relay
statements verbatim to every other player:

> **Ochre says:** "I am currently out of the office and will return Monday."

and a bounce arrives as a paragraph of SES diagnostics attributed to a human who
never typed it.

The workaround costs a round trip: filter cheaply on sender and opening line, and
only when a message looks automated re-fetch it by id to check the real flag
(`hearsay/channels/inbound.py`). Fine for us — one extra call on rare messages —
but every agent that touches email needs this and most will not know to write it.

**Suggested fix:** add `auto_generated: bool = False` and `chat_type: str | None`
to the `Message` dataclass and populate them in `_dispatch_message`. Both already
exist on the wire; this is a two-line change plus a test. Happy to send the PR.

`chat_type` is worth exposing for a second reason — it is how a handler could
tell a DM from a group without guessing, which is exactly what we spent Day 2
measuring by hand.

### 6. A conversation record does not say which channel it is on

`list_conversations()` returns `{id, connection_id, subject, created_at}`. No
channel. The channel lives on each *message* instead, so answering "what channel
is this conversation on?" means fetching its messages, or keeping your own
`connection_id -> channel` map from when you connected.

Minor, but it caught us writing the isolation probe: the obvious
`conversation.get("channel")` silently produced `?` for every row.

### 7. Isolation holds — measured, not assumed

The premise of the game is that no player can read another player's raw words.
That is not something we enforce; it is a property of how the gateway buckets
messages. `spike/isolation.py` checks the only thing that matters — whether any
conversation carries messages from more than one human sender:

```
  conv_7bdae37f938ee29521cadf3b  [email]  2 msgs  senders=['tester@agents…']
  conv_b3ba689ea57d6d8cfbaed58e  [email]  1 msgs  senders=['none'] auto=1

ISOLATION HOLDS: every conversation has at most one sender.
```

Distinct senders get distinct conversations on email, which is what we needed.
Discord is still unverified — the shared bot installs into a *server*, and
whether a DM to it opens its own conversation is not documented either way. That
is the next thing we measure, because if a server channel pools several players
into one conversation then only one seat per server is safe.

Worth saying: `auto_generated` being right on the wire is what let the probe
distinguish a bounce from a player at all. The data model is good. It is the
last hop into the handler that loses it.

---

## Day 5 — 2 August

### 8. `listen()` cannot be made restart-safe

This is the one that changed our architecture rather than just our code.

`listen()` is the idiomatic entry point and it is genuinely nice — resilient
polling, backoff, per-conversation concurrency strategies, a handler that cannot
kill the loop by raising. It also takes `from_seq`, which reads like the answer
to restarts.

The problem is getting a value to pass to it. `listen(from_seq=None)` starts from
the newest event, so anything that arrived while the process was down is skipped
in silence. To resume properly you must persist the last `seq` you handled — and
`seq` never reaches a handler. `Message` carries `id`, `conversation_id`,
`connection_id`, `customer_id`, `agent_id`, `channel`, `sender`, `subject`,
`text`, `html`, `media`. No `seq`. The event envelope has it; the object you are
handed does not.

So the cursor cannot be maintained from inside the public API. For most agents
this never surfaces: reply to what arrives, miss a message during a deploy,
nobody notices. Hearsay notices immediately — a game runs for hours across people
who answer email when they feel like it, and a dropped statement or vote stalls
the round for everyone else with no way to recover.

We run the documented custom loop instead (`SKILL.md` §15), dispatching through
`client._dispatch_event()` so the same `@client.on_message` and
`@client.on_interaction` registrations still do the work, and writing the cursor
after every event:

```python
for event in self.client.events(after_seq=cursor, limit=100):
    self.client._dispatch_event(event)
    cursor = event["seq"]
    self.store.set_cursor(cursor)
```

That means reaching for a private method to keep the public handler contract,
which is the wrong way round.

**Suggested fix:** either add `seq` to `Message` (it is already in the envelope
`_dispatch_event` unpacks), or give `listen()` an `on_cursor=` callback invoked
with each `seq` after dispatch. Either turns a restart-safe agent into a
three-line change instead of a fork of the loop.

### 9. Our own bug, recorded because it will bite others

Not the SDK's fault, but anyone building a multi-player agent on Caspian will hit
it. `concurrency="queue"` serialises messages *within a conversation*, which is
the right default and reads like enough. It is not, once one piece of state spans
several conversations: three players answering the same round are three threads
in one game. Our SQLite connection used `check_same_thread=False`, which only
silences the ownership check — it does not make the connection safe — and
concurrent access raised `InterfaceError: bad parameter or other API misuse`.

Caught by a test that puts three players on a `threading.Barrier` and releases
them together, which is exactly what happens when a round closes and everyone
replies at once. Fixed with a lock around the store and a per-game lock around
load → apply → save.

### 10. What went right

A real inbound email reached the handler, took a seat, and got a real reply back,
first try:

```
18:17:52  <- unseated [email] join: 'JOIN VFM6\r\n'
18:17:52  Ochre joined VFM6 from email
seq=3268   message.received   inbound   'JOIN VFM6\r\n'
seq=3269   message.sent       outbound  "You're in. You are Ochre. …"
```

`connect_email()` being idempotent means the agent can be restarted freely
without accumulating connections, and `test_email()` meant we could exercise the
whole inbound path without another human. Both are small things that made a
one-person project move faster than it had any right to.
