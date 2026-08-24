from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

from agents_backend.scheduling.schemas import ScheduleSpec


def next_occurrence(
    spec: ScheduleSpec,
    *,
    after: datetime,
    inclusive: bool = False,
) -> datetime | None:
    if after.tzinfo is None:
        raise ValueError("after precisa conter timezone")
    trigger = spec.trigger
    start_utc = trigger.starts_at.astimezone(UTC)
    if trigger.kind == "once":
        if start_utc > after.astimezone(UTC) or (inclusive and start_utc == after.astimezone(UTC)):
            return start_utc
        return None
    zone = ZoneInfo(trigger.timezone)
    local_start = trigger.starts_at.astimezone(zone)
    local_after = after.astimezone(zone)
    normalized = str(trigger.recurrence_rule).removeprefix("RRULE:").strip().upper()
    rule = rrulestr(normalized, dtstart=local_start)
    result = rule.after(local_after, inc=inclusive)
    if result is None:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=zone)
    result_utc = result.astimezone(UTC)
    if trigger.ends_at is not None and result_utc > trigger.ends_at.astimezone(UTC):
        return None
    return result_utc
