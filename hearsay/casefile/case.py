"""Reading a case off disk, and dealing it out so nobody holds all of it.

Cases are TOML because `tomllib` is in the standard library and evidence is
mostly multi-line prose, which TOML's triple-quoted strings carry without
ceremony. Adding a case means adding a file; no code changes.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

#: Shipped cases live here. A path can be passed instead.
CASES_DIR = Path(__file__).resolve().parent.parent.parent / "cases"


@dataclass(frozen=True)
class Stage:
    """One question, and the evidence split between the people answering it."""

    id: str
    title: str
    brief: str
    question: str
    answers: tuple[str, ...]
    hint: str
    fragments: tuple[str, ...]


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    year: str
    blurb: str
    suspects: tuple[str, ...]
    stages: tuple[Stage, ...]
    culprit: str
    explanation: str

    def stage(self, index: int) -> Stage | None:
        if 0 <= index < len(self.stages):
            return self.stages[index]
        return None

    @property
    def final_index(self) -> int:
        return len(self.stages) - 1


def load_case(path: str | Path) -> Case:
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    stages = tuple(
        Stage(
            id=s["id"],
            title=s.get("title", s["id"]),
            brief=s["brief"].strip(),
            question=s.get("question", "").strip(),
            answers=tuple(a.strip().lower() for a in s["answers"]),
            hint=s.get("hint", "").strip(),
            fragments=tuple(f.strip() for f in s["fragments"]),
        )
        for s in raw["stages"]
    )
    if not stages:
        raise ValueError(f"{path}: a case needs at least one stage")

    solution = raw.get("solution", {})
    return Case(
        id=raw["id"],
        title=raw["title"],
        year=str(raw.get("year", "")),
        blurb=raw.get("blurb", "").strip(),
        suspects=tuple(raw.get("suspects", [])),
        stages=stages,
        culprit=solution.get("culprit", "").strip(),
        explanation=solution.get("explanation", "").strip(),
    )


def find_case(name: str) -> Case:
    """By id (`ashford`) or by path. Raises with the list if it is neither."""
    candidate = Path(name)
    if candidate.exists():
        return load_case(candidate)

    shipped = CASES_DIR / f"{name}.toml"
    if shipped.exists():
        return load_case(shipped)

    available = ", ".join(sorted(p.stem for p in CASES_DIR.glob("*.toml"))) or "none"
    raise FileNotFoundError(f"no case called {name!r}. Available: {available}")


def available_cases() -> list[str]:
    return sorted(p.stem for p in CASES_DIR.glob("*.toml"))


# ------------------------------------------------------------------ answers


def _normalise(text: str) -> str:
    """Compare on words and digits only.

    People type "10:53", "10.53" and "ten fifty-three" for the same answer, and
    a stray full stop should never cost a team the round. Spelled-out numbers
    stay the author's problem — they belong in the accepted list.
    """
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def accepts(stage: Stage, answer: str) -> bool:
    given = _normalise(answer)
    if not given:
        return False
    return any(_normalise(a) == given for a in stage.answers)


# ------------------------------------------------------------------ dealing


def deal(fragments: tuple[str, ...] | list[str], seats: int) -> list[list[str]]:
    """Split the evidence so every piece is held and no one holds it all.

    Round-robin rather than chunked: with five exhibits and three investigators
    the hands come out 2/2/1 and every exhibit is somewhere, which is the only
    property that matters — a fragment nobody received is a question nobody can
    answer.

    **A case must put its keystone pair first.** Round-robin gives consecutive
    fragments to different hands for any seat count above one, so the two pieces
    that have to be combined — the clock's reading and the fact that the clock
    lies — are separated by construction. Written in the obvious order instead,
    those two landed in the same hand at three players and one investigator
    could solve the stage alone, which is the whole mode defeated.

    With more investigators than fragments the extra hands are empty, and that
    is honest: they have nothing to contribute this stage and will have to ask.
    """
    if seats < 1:
        raise ValueError("a case needs at least one investigator")
    hands: list[list[str]] = [[] for _ in range(seats)]
    for index, fragment in enumerate(fragments):
        hands[index % seats].append(fragment)
    return hands
