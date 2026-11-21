"""Protocol names for the gauntlet and the live gate, in one place.

No imports: every layer (registry, gauntlet, worker, loop, verifier) can take
these without a cycle. V61_FAMILY is what an exact-match on a verdict's
metrics.protocol accepts; the note-prefix checks that arm v6.1 rules stay
keyed on PROTOCOL_V61 alone."""

PROTOCOL_V61 = "gauntlet-protocol-v6.1"
PROTOCOL_V62 = "gauntlet-protocol-v6.2"
V61_FAMILY = (PROTOCOL_V61, PROTOCOL_V62)
NOTE_V62 = PROTOCOL_V62 + ": "

LIVE_V1 = "quarantine-live-protocol-v1"
LIVE_V2 = "quarantine-live-protocol-v2"
NOTE_LIVE_V2 = LIVE_V2 + ": "
