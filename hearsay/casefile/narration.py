"""Everything an investigator reads.

Two rules, the same ones the Hearsay copy follows. **One ask per message**,
because a person on email is half paying attention and cannot see anyone else.
**Always say where we are**, because there is no shared scrollback anywhere.

One rule of its own: the dossier mail must read like a document, not like a
notification. It is the artefact players scroll back to for the rest of the
game, and it is the reason this mode needs email at all — a chat window would
have swallowed it in four minutes.
"""

from __future__ import annotations

from hearsay.casefile.case import Case, Stage


def header(case: Case, stage_index: int, total: int) -> str:
    return f"{case.title} · stage {stage_index + 1} of {total}"


# ------------------------------------------------------------------ opening


OPENING = """**{title}** ({year})

{blurb}

---

{brief}

---

**Your evidence.** Nobody else has been sent these. Nobody else can see them \
unless you say what they are.

{hand}

---

When the team agrees, any one of you replies:  **SOLVE <answer>**"""

NO_HAND = """*You were dealt nothing this stage.* Five exhibits went to {others} \
other people. Ask them what they are holding — that is the job."""


def opening(case: Case, stage: Stage, hand: list[str], seats: int) -> str:
    return OPENING.format(
        title=case.title,
        year=case.year,
        blurb=case.blurb,
        brief=stage.brief,
        hand="\n\n".join(hand) if hand else NO_HAND.format(others=seats - 1),
    )


# -------------------------------------------------------------- next stages


NEXT_STAGE = """**{title}** — stage {n} of {total}

{brief}

---

**Your evidence.**

{hand}

---

Reply:  **{command} <answer>**"""


def next_stage(case: Case, stage: Stage, hand: list[str], index: int, final: bool,
               seats: int) -> str:
    return NEXT_STAGE.format(
        title=stage.title,
        n=index + 1,
        total=len(case.stages),
        brief=stage.brief,
        hand="\n\n".join(hand) if hand else NO_HAND.format(others=seats - 1),
        command="ACCUSE" if final else "SOLVE",
    )


# ------------------------------------------------------------------ answers


SOLVED = """**{answer}** — that is it.

{title}, stage {n} of {total}, closed. The next of the file is on its way to \
each of you."""

WRONG = """**{answer}** is not it.

{title}, stage {n} of {total}. That is {attempts} attempt{s} on this stage. \
Talk to each other — somebody is holding something you have not heard yet."""

WRONG_WITH_HINT = """**{answer}** is not it either.

{title}, stage {n} of {total}, {attempts} attempts in. Here is a push:

> {hint}"""

NOT_STARTED = "The file hasn't been opened yet. Somebody needs to send START."

ALREADY_CLOSED = "This case is closed. {culprit} did it."

EMPTY_ANSWER = "Send it with the answer:  **{command} <your answer>**"

WRONG_COMMAND = "Not yet — this stage wants **SOLVE**, not ACCUSE."

TOO_EARLY_TO_ACCUSE = (
    "You are not there yet. Stage {n} of {total} is still open, and an accusation "
    "without the earlier stages is a guess."
)


def solved(case: Case, stage: Stage, index: int, answer: str) -> str:
    return SOLVED.format(
        answer=answer.strip(), title=stage.title, n=index + 1, total=len(case.stages)
    )


def wrong(case: Case, stage: Stage, index: int, answer: str, attempts: int,
          hint_at: int) -> str:
    if attempts >= hint_at and stage.hint:
        return WRONG_WITH_HINT.format(
            answer=answer.strip(), title=stage.title, n=index + 1,
            total=len(case.stages), attempts=attempts, hint=stage.hint,
        )
    return WRONG.format(
        answer=answer.strip(), title=stage.title, n=index + 1, total=len(case.stages),
        attempts=attempts, s="" if attempts == 1 else "s",
    )


# ------------------------------------------------------------------- ending


CLOSING = """**{title}** — closed.

**{culprit}** did it.

{explanation}

---

**What you were each holding.**

{hands}

---

Nobody was sent the whole file. You had {total} exhibits between you and no way \
to see more than your own — which is why you had to say them out loud."""


def closing(case: Case, hands: dict[str, list[str]], total: int) -> str:
    lines = []
    for codename, held in hands.items():
        titles = [_first_line(fragment) for fragment in held] or ["nothing"]
        lines.append(f"**{codename}** — {', '.join(titles)}")
    return CLOSING.format(
        title=case.title,
        culprit=case.culprit,
        explanation=case.explanation,
        hands="\n".join(lines),
        total=total,
    )


def _first_line(fragment: str) -> str:
    """The exhibit's own heading, with the markdown asterisks taken off."""
    return fragment.splitlines()[0].strip().strip("*").strip()
