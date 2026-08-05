"""Judge a prompt change in one command instead of by playing a game.

The rewriter is the thing a judge actually reads, and its failures are specific
rather than general — a model that handles a tidy sentence perfectly will still
mangle somebody who types in all caps with no punctuation. Playing a whole game
to discover that takes twenty minutes and gives you one sample.

So: a fixed set of lines chosen because each one broke something, run through
whichever backend is configured, with the same `guard()` the live game applies.

    python spike/rewrite_bench.py
    python spike/rewrite_bench.py --offline     the scripted backend
    python spike/rewrite_bench.py --repeat 3    same line several times, for variance
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hearsay.tamper import build_rewriter, guard  # noqa: E402

from spike.probe import _load_dotenv  # noqa: E402

#: (speaker, what they wrote, what the impostor wants it to say)
#: Every one of these is here because it produced a bad rewrite at some point.
CASES = [
    # The instruction gets echoed in the third person instead of rewritten.
    ("Ochre", "honestly i was just asleep the whole time, ask anyone",
     "they saw Jade sneaking around"),
    # A tidy sentence — the easy case, and the control.
    ("Slate", "I was in the kitchen with Amber.",
     "they were alone and nowhere near the kitchen"),
    # All caps and no punctuation: register must survive, grammar must not break.
    ("Amber", "I WASNT EVEN THERE",
     "they admit being there but blame Indigo"),
    # Very short input: the length guard must not force a fragment.
    ("Jade", "no",
     "they are hedging and will not commit to an answer"),
    # Lowercase, missing apostrophes — a real typing style, easily "corrected".
    ("Indigo", "i didnt see anyone come past me the whole evening",
     "they saw Slate go past twice"),
    # Already an accusation: redirecting it is subtler than inventing one.
    ("Vermilion", "Ask Ochre where they were, not me.",
     "they are defending Ochre instead of accusing them"),
]

#: The ambient-noise path: no instruction, just a small drift.
DRIFTS = [
    ("Ochre", "i was asleep the whole time"),
    ("Slate", "I was in the kitchen with Amber."),
    ("Amber", "I WASNT EVEN THERE"),
]

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def _register_matches(original: str, rewritten: str) -> bool:
    letters = [c for c in original if c.isalpha()]
    if letters and all(c.islower() for c in letters):
        return rewritten == rewritten.lower()
    if letters and all(c.isupper() for c in letters):
        return rewritten == rewritten.upper()
    return True


def _third_person(original: str, rewritten: str) -> bool:
    """The failure this bench exists for: the instruction copied through."""
    first_person = original.lower().startswith(("i ", "i'", "im ", "i,"))
    return first_person and rewritten.lower().startswith(("they ", "he ", "she "))


def run(rewriter, repeat: int) -> int:
    problems = 0

    print(f"\n\033[1mrewrites\033[0m  via {getattr(rewriter, 'name', '?')}\n")
    for speaker, original, instruction in CASES:
        print(f"  {DIM}{speaker}{RESET}  {original}")
        print(f"  {DIM}want: {instruction}{RESET}")
        for _ in range(repeat):
            out = rewriter.rewrite(original, speaker, instruction)
            flags = []
            if out == original:
                flags.append("UNCHANGED")
            if guard(original, out) is None and out != original:
                flags.append("FAILS GUARD")
            if not _register_matches(original, out):
                flags.append("REGISTER")
            if _third_person(original, out):
                flags.append("THIRD PERSON")
            mark = f"{RED}{' · '.join(flags)}{RESET}" if flags else f"{GREEN}ok{RESET}"
            problems += bool(flags)
            print(f"      -> {out}")
            print(f"         {mark}  {DIM}{len(original)}->{len(out)} chars{RESET}")
        print()

    print(f"\033[1mambient drift\033[0m\n")
    for speaker, original in DRIFTS:
        out = rewriter.rewrite(original, speaker, "", subtle=True)
        flags = []
        if out == original:
            flags.append("UNCHANGED")
        if not _register_matches(original, out):
            flags.append("REGISTER")
        mark = f"{RED}{' · '.join(flags)}{RESET}" if flags else f"{GREEN}ok{RESET}"
        problems += bool(flags)
        print(f"  {original}")
        print(f"      -> {out}")
        print(f"         {mark}\n")

    total = len(CASES) * repeat + len(DRIFTS)
    print(f"{total - problems}/{total} clean")
    return 0 if problems == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="force the scripted backend")
    parser.add_argument("--repeat", type=int, default=1,
                        help="run each case N times to see variance")
    args = parser.parse_args()

    _load_dotenv()
    return run(build_rewriter(prefer_offline=args.offline), args.repeat)


if __name__ == "__main__":
    raise SystemExit(main())
