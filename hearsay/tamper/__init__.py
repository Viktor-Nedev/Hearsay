"""Changing what somebody said, in their own voice, on the way to everyone else."""

from hearsay.tamper.rewriter import Rewriter, build_rewriter, guard
from hearsay.tamper.scripted import ScriptedRewriter

__all__ = ["Rewriter", "ScriptedRewriter", "build_rewriter", "guard"]
