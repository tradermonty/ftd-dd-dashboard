"""Hard-coded design / test split (spec §3.6). Selection may only use the design period."""

from __future__ import annotations

from datetime import date, timedelta

DESIGN_START = "2000-01-03"
DESIGN_END = "2014-12-31"
TEST_START = "2015-01-02"
# End of the evaluated test period, frozen on 2026-11-07 (the last session of the data when the
# reviews of R and R2 were done). It is a constant, not "the latest session": sessions after it
# are never part of the test period. They go only into the forward record (the sessions nobody
# had seen when the end was frozen), for R and R2 alike. The publish gate checks that the
# recorded test end equals this constant.
TEST_END = "2026-11-06"

HORIZON = 60  # forward window (sessions) for trades and drawdown events


def day_after(d: str) -> str:
    """The calendar day after `d` (ISO string): the first date that can lie in the forward
    record when `d` is the end of the test period."""
    return (date.fromisoformat(d) + timedelta(days=1)).isoformat()
