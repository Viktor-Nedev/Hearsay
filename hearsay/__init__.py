"""Hearsay — a social deduction game where the messenger is the impostor.

Players never share a channel. The agent is the only path between them, which is
exactly what makes tampering undetectable from the inside.
"""

from __future__ import annotations

__all__ = ["__version__", "sdk_version"]

__version__ = "0.1.0"


def sdk_version() -> str:
    """Version of caspian-sdk we are running against.

    The package exposes no ``__version__`` (see FIELDNOTES.md), and gateway
    behaviour depends on which SDK build is talking to it, so read the installed
    distribution metadata instead.
    """
    try:
        from importlib.metadata import version

        return version("caspian-sdk")
    except Exception:  # pragma: no cover - only if the dist metadata is missing
        return "unknown"
