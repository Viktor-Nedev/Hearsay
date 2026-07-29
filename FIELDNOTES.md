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
