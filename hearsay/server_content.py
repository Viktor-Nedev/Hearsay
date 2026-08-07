"""What the agent posts into a Discord server to furnish it.

The SDK cannot create channels, so the server is built by hand and filled by
ritual: somebody posts `SETUP <section>` in a channel and the agent publishes
that section there. It can only speak into a channel it has already seen a
message in, which makes the ritual the requirement rather than a workaround.

Copy is written for someone who has never seen this before and is reading it in
a Discord sidebar, so: short lines, one idea per paragraph, and the commands
made obvious enough to copy without reading the prose around them.
"""

from __future__ import annotations

RULES = """# Hearsay

**Every other AI agent talks to you. This one talks between people.**

Five players. Nobody shares a channel — one of you is on Discord, one is reading \
email, one is somewhere else entirely. You cannot see each other. Every word \
that travels between you is carried by the agent.

That is the whole idea, and both games here are built on it: **the agent is the \
only thing that sees everything.**

In **Casefile** it uses that to divide. Each investigator is emailed a different \
part of the evidence, so nobody can solve it alone.

In **Hearsay** it uses that to lie. One player can change what somebody *else* \
said, on the way to everyone else, in that person's own voice.

---

**One rule holds in both.** What arrives in your inbox is yours alone. Nobody \
else was sent it, and nobody will ever see it unless you say what it is.

Talk. It is the only move there is.

*Start in #how-to-play.*"""


HOW_TO_PLAY = """# How to play

Everything happens by writing to the agent. Post here on Discord, or email \
**hearsay@agents.trycaspianai.com** — it is the same agent either way, and the \
game does not care which you use.

## Taking a seat

```
JOIN <code>      take a seat in that game
WHO              how many are in
LEAVE            give up your seat
HELP             this, shorter
```

A game needs **three seats** before it can begin. Any player can then send:

```
START
```

## Casefile — the investigation

```
SOLVE <answer>     when the team agrees
ACCUSE <name>      only on the final stage
```

The evidence arrives by email, split between you. Read yours, say what it is, \
listen to what the others have. No single fragment answers anything.

Wrong answers cost nothing but time. A hint arrives on the third one.

## Hearsay — the deception

```
VOTE <name>                    or tap the button, on Discord
TAMPER <name> <what it says>   only the impostor, once a round
```

Everyone writes a line. The agent carries all of them to everyone else — except \
that one of you can rewrite somebody's line first, and the person who wrote it \
goes on reading their own words as they typed them.

They find out when the room quotes something at them they never said.

## A word on email

**Reply to the agent's messages — do not compose new ones.** A new email starts \
a new conversation, and a conversation is a seat. Reply, and you stay yourself."""


CASEFILE = """# Casefile

*A murder, an archive, and five people holding different pages of it.*

**The Ashford Vigil (1937).** Sir Edmund Ashford is found dead in his study \
during the annual vigil for his late wife. The storm took the Bramley road down \
by nine; nobody arrived after that and nobody left. Five people were in the \
house.

## How it runs

You open a case and get a code. Everyone joins with it, from wherever they are.

When it starts, **the agent emails each of you a different part of the file.** \
Not a summary — the actual exhibits. A coroner's note. A telephone log. A page \
from somebody's diary.

You have some of them. You do not have the others. Neither does anyone else.

Then you talk here, and find out what everybody else is holding.

## Three stages

**The hour** — when did he actually die?
**The alibi** — whose account does not survive that hour?
**The reason** — and why.

Each one opens the next by email. When you name the murderer, the agent finally \
sends everyone the *whole* file, including the pages you never saw.

## Why it is built this way

The evidence goes to email because evidence should persist — you scroll back to \
a document, you do not scroll back to a chat. The talking happens here because \
talking should be fast and disposable.

Put all of it in one place and the mode stops working. That is the point.

```
JOIN <code>        then      SOLVE <answer>
```"""


HEARSAY = """# Hearsay

*A game about whether the messenger is telling you the truth.*

Everyone writes one line a round. The agent carries every line to everyone else.

One of you is the **Impostor**. Their power is not to lie — it is to change what \
somebody *else* said, in transit, in that person's own voice.

## The part that makes it hurt

**The author reads their own words, unchanged.** Everyone else reads the rewrite.

So you can be voted out for a sentence you never wrote, and you cannot prove you \
did not write it, because the only witness to what you actually said is the \
thing that changed it.

You find out when the room turns on you for something that was never yours.

## Ambient noise

The agent also garbles a line now and then that nobody asked it to. Without that, \
every rewrite would be provably the impostor's and the game would collapse into \
one accusation.

So when somebody swears they did not say it — they might be right.

## A round

Statements, then discussion, then a vote. Witnesses win by voting the impostor \
out. The impostor wins by surviving until only two are left.

Afterwards the agent shows everyone every rewrite it made, and who read what.

```
JOIN <code>       then      VOTE <name>
```"""


PRIVATE = """# Private rooms

There is nothing to set up.

**A game code is the room.** Open a game, share the code with three friends and \
nobody else, and that is a private table — the agent will only seat people who \
have the code, and it never announces games anywhere.

```
JOIN K7QP
```

Two people with two different codes are in two different games, even if they are \
typing in this same channel. The agent seats you by the conversation you write \
from, not by where you happen to be standing.

Which also means: **you can play from anywhere.** Discord, email, or one of each. \
Your friend never has to install anything or make an account.

*Ask in the lobby channels for a code, or open your own.*"""


#: What `SETUP <section>` accepts. Keys are what people type.
SECTIONS = {
    "rules": RULES,
    "how-to-play": HOW_TO_PLAY,
    "howtoplay": HOW_TO_PLAY,
    "play": HOW_TO_PLAY,
    "casefile": CASEFILE,
    "hearsay": HEARSAY,
    "private": PRIVATE,
    "rooms": PRIVATE,
}

UNKNOWN_SECTION = (
    "I don't have a section called **{name}**.\n\n"
    "Available: {available}\n\n"
    "Post `SETUP <section>` in the channel you want it published to."
)


def section(name: str) -> str | None:
    return SECTIONS.get((name or "").strip().lower().lstrip("#"))


def available() -> str:
    seen: list[str] = []
    for key, body in SECTIONS.items():
        if not any(body is SECTIONS[k] for k in seen):
            seen.append(key)
    return ", ".join(f"`{name}`" for name in seen)
