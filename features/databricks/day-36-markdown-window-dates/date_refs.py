"""Day 36 (Databricks): a local resolver for Lakeflow Jobs dynamic value references with date arithmetic (Beta,
platform release notes 2026-11-09), and the parameters the daily markdown-planning job uses.

Documented syntax (Jobs docs, "Dynamic value references" > "Date arithmetic"):

    {{(<time_reference> <+ or -> <amount>.<unit>).<argument>}}

time_reference: job.start_time or job.trigger.time (UTC); unit: minutes, hours, days, weeks, months or years (singular
or plural); argument: iso_weekday, is_weekday, iso_date, iso_datetime, year, month, day, hour, minute, second,
timestamp_ms. A duration may be at most 2,000 years, and a result before 0001-01-01T00:00:00.000Z or after
9999-12-31T23:59:59.999Z fails the task. Without the parentheses it's the plain reference, e.g.
{{job.start_time.iso_date}}.

Not documented, so assumed here (and checked by the cloud run on whatever day it runs): months and years move the
calendar field and clamp to the last day of a shorter month (2026-03-31 - 1.month = 2026-02-28), like java.time.
Stdlib only: the cloud run uses this to check the values the job received.
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone

MIN = datetime(1, 1, 1, tzinfo=timezone.utc)
MAX = datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc)
MAX_YEARS = 2000
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_REF = r"job\.start_time|job\.trigger\.time"
_UNITS = {"minute": "minutes", "hour": "hours", "day": "days", "week": "weeks", "month": "months", "year": "years"}
PATTERN = re.compile(
    r"^\{\{\s*(?:\(\s*(?P<ref>" + _REF + r")\s*(?P<sign>[+-])\s*(?P<amount>\d+)\.(?P<unit>[a-z]+)\s*\)"
    r"|(?P<plain>" + _REF + r"))\.(?P<arg>[a-z_]+)\s*\}\}$")
ARGS = ("iso_weekday", "is_weekday", "iso_date", "iso_datetime", "year", "month", "day", "hour", "minute", "second",
        "timestamp_ms")

# The job's task parameters: name -> reference. job.start_time so the check has an exact instant to resolve against.
PARAMS = {
    "start_ms": "{{job.start_time.timestamp_ms}}",
    "run_date": "{{job.start_time.iso_date}}",
    "yesterday": "{{(job.start_time - 1.day).iso_date}}",
    "yesterday_last_week": "{{(job.start_time - 8.days).iso_date}}",
    "week_start": "{{(job.start_time - 1.week).iso_date}}",
    "sell_by_cutoff": "{{(job.start_time + 3.days).iso_date}}",
    "last_month": "{{(job.start_time - 1.month).month}}",
    "last_month_year": "{{(job.start_time - 1.month).year}}",
    "same_day_last_year": "{{(job.start_time - 1.year).iso_date}}",
    "six_hours_ago_ms": "{{(job.start_time - 6.hours).timestamp_ms}}",
    "ninety_min_ago_hour": "{{(job.start_time - 90.minutes).hour}}",
    "tomorrow_iso_weekday": "{{(job.start_time + 1.day).iso_weekday}}",
}


class RefError(ValueError):
    pass


def _add_months(t: datetime, months: int) -> datetime:
    y, m = divmod(t.month - 1 + months, 12)
    year, month = t.year + y, m + 1
    if not 1 <= year <= 9999:
        raise RefError("result out of range")
    return t.replace(year=year, month=month, day=min(t.day, calendar.monthrange(year, month)[1]))


def shift(t: datetime, sign: str, amount: int, unit: str) -> datetime:
    unit = _UNITS.get(unit, unit)
    if unit not in _UNITS.values():
        raise RefError(f"unknown unit {unit!r}")
    n = amount if sign == "+" else -amount
    limits = {"minutes": MAX_YEARS * 525_960, "hours": MAX_YEARS * 8766, "days": 730_485, "weeks": 730_485 // 7,
              "months": MAX_YEARS * 12, "years": MAX_YEARS}
    if amount > limits[unit]:
        raise RefError("duration over 2,000 years")
    try:
        if unit == "years":
            out = _add_months(t, 12 * n)
        elif unit == "months":
            out = _add_months(t, n)
        else:
            out = t + timedelta(**{unit: n})
    except OverflowError as e:
        raise RefError("result out of range") from e
    if not MIN <= out <= MAX:
        raise RefError("result out of range")
    return out


def fmt(t: datetime, arg: str) -> str:
    if arg == "iso_date":
        return t.date().isoformat()
    if arg == "iso_datetime":
        return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"
    if arg == "timestamp_ms":
        return str((t - EPOCH) // timedelta(milliseconds=1))
    if arg == "iso_weekday":
        return str(t.isoweekday())
    if arg == "is_weekday":
        return "true" if t.isoweekday() <= 5 else "false"
    if arg in ("year", "month", "day", "hour", "minute", "second"):
        return str(getattr(t, arg))
    raise RefError(f"unknown argument {arg!r}")


def resolve(ref: str, start: datetime, trigger: datetime | None = None) -> str:
    m = PATTERN.match(ref)
    if not m:
        raise RefError(f"not a time reference: {ref!r}")
    name = m["ref"] or m["plain"]
    t = start if name == "job.start_time" else (trigger or start)
    if m["ref"]:
        t = shift(t, m["sign"], int(m["amount"]), m["unit"])
    return fmt(t, m["arg"])


def resolve_all(start: datetime, params: dict | None = None) -> dict:
    return {k: resolve(v, start) for k, v in (params or PARAMS).items()}


def from_ms(ms: int | str) -> datetime:
    return EPOCH + timedelta(milliseconds=int(ms))
