"""Spaced-repetition scheduling shared by topics and vocabulary.

Stage 0 is "not yet remembered". Each successful review moves the item up one stage
(two for "easy"), and the next review is due after INTERVALS_DAYS[stage - 1] days.
"""

from datetime import datetime, time, timedelta, timezone

INTERVALS_DAYS = [1, 3, 7, 14, 30, 60]
MAX_STAGE = len(INTERVALS_DAYS)
GRADES = ("again", "hard", "good", "easy")


def due_in_days(days: int, now: datetime | None = None) -> str:
    """The start (local midnight) of the learner's calendar day `days` after `now`, as a UTC ISO string.
    Days are calendar days, not 24-hour spans: a topic studied at 21:00 is due all of the next day, and
    its due time does not drift later with each review. Local means the computer's clock, as in
    store.local_today()."""
    local_date = (now or datetime.now(timezone.utc)).astimezone().date()
    # A naive datetime's astimezone() reads it as local time, with the offset of that date (summer time)
    due = datetime.combine(local_date + timedelta(days=days), time.min).astimezone(timezone.utc)
    return due.isoformat(timespec="seconds")


def schedule(stage: int, grade: str, *, again_after: timedelta = timedelta(days=1),
             now: datetime | None = None) -> tuple[int, str]:
    """Return (new_stage, due_at as ISO string) after a review with the given grade."""
    if grade not in GRADES:
        raise ValueError(f"grade must be one of {GRADES}")
    now = now or datetime.now(timezone.utc)

    if grade == "again":
        # An exact delay: a word forgotten now comes back in the same study block, not at once
        return 0, (now + again_after).isoformat(timespec="seconds")
    if grade == "hard":
        # Stay on the same stage and come back sooner than a normal success would
        days = 1 if stage == 0 else max(1, INTERVALS_DAYS[stage - 1] // 2)
        return stage, due_in_days(days, now)

    new_stage = min(stage + (2 if grade == "easy" else 1), MAX_STAGE)
    days = INTERVALS_DAYS[new_stage - 1]
    return new_stage, due_in_days(days, now)
