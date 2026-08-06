"""Casefile — the mode where the agent divides the evidence instead of altering it.

In Hearsay the agent's monopoly on information is used to lie. Here the same
monopoly is used to make lying pointless: no investigator holds enough of the
file to answer anything alone, so the only way through is to say out loud what
you were given.
"""

from hearsay.casefile.case import Case, Stage, accepts, deal, find_case, load_case

__all__ = ["Case", "Stage", "accepts", "deal", "find_case", "load_case"]
