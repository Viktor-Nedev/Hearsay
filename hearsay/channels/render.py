"""Making one message look native on two very different surfaces.

This is the only module that knows Discord can do more than text, and it changes
nothing about *what* is said — only how it arrives.

Worth being precise about why it matters beyond looks. The hackathon rule is one
handler across two channels, and this is where that becomes visible: the same
`Deliver` effect leaves the engine as a sentence and arrives as a card with
tappable buttons in Discord and as `VOTE <name>` in an inbox, with nothing
upstream aware of the difference.

Only the vote is rendered. Everything else in the game is prose and reads well as
prose on both channels; voting is a choice from a short list, which is what
buttons are for. Rendering the rest would be decoration, and a card flattened
into an email reads worse than a paragraph written to be one.

The engine stays channel-ignorant: it marks a payload `kind="vote"` and this
module decides what that means for a given seat.
"""

from __future__ import annotations

from caspian_sdk import blocks as b

from hearsay.engine.state import GameState, SeatView
from hearsay.transport import Payload

#: Interaction callback prefix. `Driver.on_interaction` splits on the colon.
VOTE = "vote"

#: Payload kinds the engine marks. Anything else is delivered as written.
KIND_VOTE = "vote"


def vote_payload(state: GameState, seat: SeatView, text: str) -> Payload:
    """The vote, as buttons, for a channel that has them.

    `text` is kept as the fallback body: a channel that cannot render blocks
    still gets the full instructions, and so does anyone whose client hides
    them.
    """
    candidates = [s for s in state.alive if s.id != seat.id]
    if not candidates:
        return Payload(text)

    return Payload(
        text,
        blocks=[
            b.card(
                title="Who is the impostor?",
                subtitle=f"Round {state.round} · {len(state.alive)} still in",
                text="One tap. You can change your mind until everyone has voted.",
                buttons=[
                    {"label": s.codename, "value": f"{VOTE}:{s.codename}"}
                    for s in candidates
                ],
            )
        ],
    )


def enrich(payload: Payload, kind: str, state: GameState, seat: SeatView,
           supports_buttons: bool) -> Payload:
    """Upgrade a payload for this seat, or hand it back untouched."""
    if kind == KIND_VOTE and supports_buttons:
        return vote_payload(state, seat, payload.text)
    return payload
