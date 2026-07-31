"""Everything that knows a channel exists. The engine deliberately does not."""

from hearsay.channels.outbox import CapabilityMatrix, Outbox

__all__ = ["CapabilityMatrix", "Outbox"]
