"""Observation-age policy, not a measurement of stock firmware boot time."""

# The canary owner reports a five-minute receive window after battery connection.
# HA's cached sighting is not a boot event: this only permits a bounded attempt,
# and can also include a tag which has already gone to sleep.
MAX_ADVERTISEMENT_AGE = 300.0
FRESH_ADVERTISEMENT_AGE = 10.0


def observation_state(age: float) -> str:
    """Keep cached eligibility distinct from a fresh radio observation."""
    if 0 <= age <= FRESH_ADVERTISEMENT_AGE:
        return "advertising"
    if 0 <= age <= MAX_ADVERTISEMENT_AGE:
        return "recently_seen"
    return "waiting_for_window"
