# Setting up the Discord server

The Caspian SDK sends and receives; it cannot create channels. So the server is
built by hand once, and then filled by the agent.

Ten minutes of clicking, then four messages.

---

## 1. The layout

Create these in Discord. Categories are the grey headers; the rest are text
channels.

```
📋 START HERE
   #rules                what this is
   #how-to-play          the commands

🕵️ CASEFILE
   #case-lobby           ask for a code, or open one
   #room-1
   #room-2

🎭 HEARSAY
   #hearsay-lobby
   #table-1
   #table-2
```

**Permissions.** The agent's only requirement is *View Channel* and *Send
Messages* wherever you want it to speak. For `#rules` and `#how-to-play`, take
*Send Messages* away from `@everyone` **after** the setup step below — otherwise
you cannot post the command that fills them.

**Private rooms need nothing here.** A game code shared with three friends and
nobody else already is a private table; the agent never announces a game and
only seats people who have the code. Extra channels are for tidiness, not
privacy.

---

## 2. Filling it

The agent can only speak into a channel it has already seen a message in — that
is how a Discord channel becomes a conversation it knows about. So the ritual is
the requirement.

With the agent running, post in each channel:

| Channel | Post |
|---|---|
| `#rules` | `SETUP rules` |
| `#how-to-play` | `SETUP how-to-play` |
| `#case-lobby` | `SETUP casefile` |
| `#hearsay-lobby` | `SETUP hearsay` |
| anywhere you like | `SETUP private` |

Each publishes that section into the channel you posted from. Get the name
wrong and it tells you what it has.

Content lives in [`hearsay/server_content.py`](../hearsay/server_content.py) —
edit there and re-post to update.

---

## 3. Playing

Open a game from the terminal:

```bash
python -m hearsay.app --new --mode casefile --case ashford
python -m hearsay.app --new --bots 3
```

It prints a code. Anyone posts `JOIN <code>` in any channel, or emails it to
`hearsay@agents.trycaspianai.com`. Both are the same agent and the same game.

---

## An open question

Our Discord conversation reports `chat_type=guild`, and we have not yet measured
whether a **second channel** in the same server produces a **second
conversation**.

It matters for one thing only: whether two players sitting in different Discord
channels get different seats. If the whole guild is one conversation they would
share a seat, and a Discord table is limited to one player with everyone else on
email.

The game itself is unaffected either way — it addresses seats, never channels.

To find out: post in a second channel and run

```bash
python spike/isolation.py
```

A new `[discord]` row means rooms are real.
