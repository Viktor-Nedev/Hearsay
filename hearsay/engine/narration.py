"""Every word a player reads.

Kept apart from the machine for two reasons: wording can be tuned without
touching a state transition, and tests can assert on structure rather than prose.

Two rules hold everywhere in this file.

**One ask per message.** A player is on email, on a phone, half paying attention,
and cannot see anybody else. If a message asks for two things, one of them will
not arrive. Every prompt ends with a single instruction.

**Always say where we are.** Every message opens with a header — round, phase,
how many are left — because a player who has been away for an hour has no other
way to orient. There is no scrollback shared with anyone.
"""

from __future__ import annotations

from hearsay.engine.rules import IMPOSTOR
from hearsay.engine.state import GameState, Phase, SeatView

_PHASE_WORDS = {
    Phase.BRIEF: "briefing",
    Phase.STATEMENT: "statements",
    Phase.RELAY: "relay",
    Phase.DELIBERATE: "discussion",
    Phase.VOTE: "voting",
    Phase.REVEAL: "reveal",
    Phase.GAMEOVER: "over",
}


def header(state: GameState) -> str:
    """`Round 2 · voting · 4 still in`"""
    word = _PHASE_WORDS.get(state.phase, state.phase.value.lower())
    if state.phase is Phase.GAMEOVER:
        return "Game over"
    return f"Round {state.round} · {word} · {len(state.alive)} still in"


def _wrap(state: GameState, body: str) -> str:
    return f"{header(state)}\n\n{body}"


# ------------------------------------------------------------------ brief


WITNESS_BRIEF = (
    "You are {codename}. You are a **witness**.\n\n"
    "One of the {count} of you is an impostor. Nobody knows who. You cannot see "
    "each other — you are each on a different app, and I carry every word between "
    "you.\n\n"
    "Find the impostor and vote them out. That is all you have to do."
)

IMPOSTOR_BRIEF_HONEST = (
    "You are {codename}. **You are the impostor.**\n\n"
    "The other {others} are witnesses. They are looking for you. They cannot see "
    "each other and neither can you — everything anyone says reaches the rest "
    "through me.\n\n"
    "Survive the vote. Be ordinary."
)

IMPOSTOR_BRIEF = (
    "You are {codename}. **You are the impostor.**\n\n"
    "The other {others} are witnesses, and they cannot see each other. Every word "
    "between them passes through me — which means you can change one.\n\n"
    "Once per round, before I relay anything, you may tell me to rewrite what "
    "somebody said. They will never see the original. Neither will anyone else.\n\n"
    "Survive the vote."
)


def brief(state: GameState, seat: SeatView) -> str:
    if seat.role == IMPOSTOR:
        template = IMPOSTOR_BRIEF_HONEST if state.honest else IMPOSTOR_BRIEF
        body = template.format(codename=seat.codename, others=len(state.alive) - 1)
    else:
        body = WITNESS_BRIEF.format(codename=seat.codename, count=len(state.alive))
    return _wrap(state, body)


# -------------------------------------------------------------- statement


STATEMENT_PROMPT = (
    "Everyone is about to speak, one line each, and I will carry all of it to "
    "everyone else.\n\n"
    "Say something. Accuse somebody, defend yourself, or give an alibi — it is "
    "your only channel to the rest of them.\n\n"
    "**Reply with what you want the others to hear.**"
)

DELIBERATE_PROMPT = (
    "You have all heard each other. Now you get one more line before the vote.\n\n"
    "**Reply with anything you want to add.**"
)


def prompt(state: GameState) -> str:
    body = STATEMENT_PROMPT if state.phase is Phase.STATEMENT else DELIBERATE_PROMPT
    return _wrap(state, body)


ACK_STATEMENT = "Got it. Waiting on {remaining} more, then I'll pass it all on."
ACK_LAST = "Got it — that was the last one."


def acknowledge(state: GameState, remaining: int) -> str:
    return ACK_LAST if remaining == 0 else ACK_STATEMENT.format(remaining=remaining)


# ------------------------------------------------------------------ relay


def transcript(state: GameState, lines: list[tuple[str, str]], intro: str) -> str:
    """`lines` is (codename, text) in seat order.

    A player sees their own line too. That is deliberate: when the tamper step
    lands, the victim reads their own words altered and knows immediately — and
    can prove nothing. "I never said that" is the most interesting sentence in
    the game, and it only exists if you can see what you supposedly said.
    """
    rendered = "\n\n".join(f"**{codename}:** {text}" for codename, text in lines)
    return _wrap(state, f"{intro}\n\n{rendered}")


RELAY_INTRO = "Here is what everyone said."
RELAY_INTRO_DELIBERATE = "Last words before the vote."


# ----------------------------------------------------------------- tamper


TAMPER_PROMPT = (
    "Everyone has spoken. Nobody has seen anything yet — it all goes through me, "
    "and I am showing you first.\n\n"
    "{lines}\n\n"
    "You may change one of these before I pass it on. They will still see it "
    "under the name of whoever wrote it, and that person will go on reading "
    "their own words, so they will not know until somebody quotes them back.\n\n"
    "**Reply with:  TAMPER <name> <what you want it to say>**\n"
    "or **SKIP** to let it all through."
)

TAMPER_DONE = "Done. {codename} will appear to have said something else."
TAMPER_SKIPPED = "Passing it all through untouched."
TAMPER_UNKNOWN = "There's nobody called {target}. You can change: {names}"
TAMPER_SELF = "Changing your own line would only make you look tampered with. Pick someone else."
TAMPER_FAILED = (
    "I couldn't make that read naturally, so I left it alone rather than "
    "send something that would give us both away."
)
TAMPER_NOT_NOW = "Nothing to change right now. I'll show you the statements when there are some."


def tamper_prompt(state: GameState, statements: list[tuple[str, str]]) -> str:
    lines = "\n\n".join(f"**{codename}:** {text}" for codename, text in statements)
    return _wrap(state, TAMPER_PROMPT.format(lines=lines))


WAITING_ON_RELAY = "Everyone's in. One moment."


# ------------------------------------------------------------------- vote


VOTE_PROMPT = (
    "Time to vote. One of you is the impostor.\n\n"
    "Still in: {names}\n\n"
    "**Reply with:  VOTE <name>**"
)


def vote_prompt(state: GameState, seat: SeatView) -> str:
    names = ", ".join(s.codename for s in state.alive if s.id != seat.id)
    return _wrap(state, VOTE_PROMPT.format(names=names))


VOTE_ACK = "Vote recorded: {target}. Waiting on {remaining} more."
VOTE_ACK_LAST = "Vote recorded: {target}. That's everyone — counting now."

VOTE_UNKNOWN = "There's nobody called {target} in this game. Still in: {names}"
VOTE_SELF = "You can't vote for yourself. Still in: {names}"
VOTE_DEAD = "{target} is already out. Still in: {names}"
VOTE_NOT_NOW = "It isn't voting time yet. I'll ask when it is."


def vote_error(template: str, state: GameState, seat: SeatView, target: str = "") -> str:
    names = ", ".join(s.codename for s in state.alive if s.id != seat.id)
    return _wrap(state, template.format(target=target, names=names))


# ----------------------------------------------------------------- reveal


ELIMINATED = "The vote fell on **{codename}**.\n\n{tally}\n\n{aftermath}"
ELIMINATED_YOU = "The vote fell on you.\n\n{tally}\n\nYou're out. Watch how it ends."
DEADLOCK = "Nobody had a majority. No one goes.\n\n{tally}\n\nAnother round, then."


def tally_lines(counts: dict[str, int]) -> str:
    if not counts:
        return "No votes were cast."
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return "\n".join(f"{name}: {n}" for name, n in ranked)


def reveal(state: GameState, seat: SeatView, eliminated: SeatView | None,
           counts: dict[str, int]) -> str:
    tally = tally_lines(counts)
    if eliminated is None:
        return _wrap(state, DEADLOCK.format(tally=tally))
    if eliminated.id == seat.id:
        return _wrap(state, ELIMINATED_YOU.format(tally=tally))
    # Never say what the eliminated player's role was. That is the whole game.
    return _wrap(state, ELIMINATED.format(
        codename=eliminated.codename, tally=tally,
        aftermath="I'm not going to tell you whether that was the right call.",
    ))


# --------------------------------------------------------------- game over


WITNESSES_WON = (
    "The witnesses win.\n\n"
    "**{impostor}** was the impostor.\n\n"
    "{closing}"
)

IMPOSTOR_WON = (
    "The impostor wins.\n\n"
    "**{impostor}** was the impostor, and got away with it.\n\n"
    "{closing}"
)

CLOSING_HONEST = "Nothing said in this game was altered. This time."


def game_over(state: GameState, winner: str, closing: str = CLOSING_HONEST) -> str:
    impostor = state.impostor
    template = IMPOSTOR_WON if winner == IMPOSTOR else WITNESSES_WON
    return _wrap(state, template.format(
        impostor=impostor.codename if impostor else "Nobody",
        closing=closing,
    ))


# ---------------------------------------------------------------- nudges


WAITING = "Still waiting on you.\n\n{ask}"
NOT_YOUR_TURN = "Nothing needed from you right now. I'll write when there is."
ELIMINATED_SILENCE = "You're out — you can watch, but you can't speak."
TOO_FEW = "Not enough players yet. {need} more before this can start."
