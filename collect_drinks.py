#!/usr/bin/env python3
"""Drinks collector for `habits` — dry nights, from Window's drinks log.

Writes state/drinks.json. Facts live in the app, judgment lives here.

    ~/Library/Mobile Documents/iCloud~com~alexpriest~window/Documents/drinks.jsonl
    {"at":"2026-10-06T21:14:00-05:00","id":"…","kind":"vermouth","source":"app"}

Append-only. The same `id` appended again supersedes the earlier line (last wins);
a `{"deleted":true,"id":…}` line is terminal for that id, so a later drink line
with the same id does not bring it back. Unparseable lines (a half-written tail)
are skipped.

THE 4AM RULE: a drink belongs to the evening it was poured. The date comes from
the timestamp's own local offset, and anything before 04:00 counts toward the
previous calendar date. Tonight therefore closes at 4am tomorrow, so today is
never "dry" yet.

TRACKING_SINCE: the cut began 2026-10-06. Earlier days are unknown, never dry.
The 3-dry-nights bar and the 2-per-weeknight figure are protocol (Asa's plan)
and live here, so changing them is an edit to this file rather than an app build.
`weeknights_over` is reported for information; it is not the row's verdict.

DEGRADED RULE: an iCloud placeholder (`.drinks.jsonl.icloud`) or an unreadable
file means the data exists but cannot be read. Change nothing, exit non-zero, and
let the prior state age. A missing file is ABSENT (app not shipped): state is
written with no value rather than inventing dry nights.
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path.home()
DRINKS = (
    HOME / "Library" / "Mobile Documents" / "iCloud~com~alexpriest~window"
    / "Documents" / "drinks.jsonl"
)
# A copy of the raw log so Asa can read it and history survives an iCloud reset.
RAW_COPY = HOME / "Obsidian" / "alexpriest" / "Claude" / "Coach" / "Data" / "drinks.jsonl"
STATE = HOME / "Code" / "tools" / "habits" / "state" / "drinks.json"

WINDOW_DAYS = 7
TRACKING_SINCE = date(2026, 10, 6)
EVENING_CUTOFF_HOUR = 4
WEEKNIGHT_MAX = 2  # Mon-Thu evenings

OK = "ok"
ABSENT = "absent"
DEGRADED = "degraded"


def read_drinks(path: Path = DRINKS) -> tuple[list[dict], str]:
    """Return (records, status). DEGRADED means change nothing downstream."""
    if not path.exists():
        if path.with_name(f".{path.name}.icloud").exists():
            return [], DEGRADED
        return [], ABSENT
    try:
        text = path.read_text()
    except OSError:
        return [], DEGRADED

    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and "id" in rec:
            out.append(rec)
    return out, OK


def resolve(records: list[dict]) -> list[dict]:
    """Last record per id wins; a deletion is terminal for its id."""
    latest: dict[str, dict] = {}
    deleted: set[str] = set()
    for rec in records:
        sid = str(rec["id"])
        if rec.get("deleted"):
            deleted.add(sid)
        else:
            latest[sid] = rec
    return [rec for sid, rec in latest.items() if sid not in deleted]


def evening_of(stamp: object) -> date | None:
    try:
        moment = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    day = moment.date()  # wall-clock date in the timestamp's own offset
    return day - timedelta(days=1) if moment.hour < EVENING_CUTOFF_HOUR else day


def evening_counts(drinks: list[dict]) -> dict[date, int]:
    counts: dict[date, int] = {}
    for rec in drinks:
        day = evening_of(rec.get("at"))
        if day is not None:
            counts[day] = counts.get(day, 0) + 1
    return counts


def window(today: date) -> list[date]:
    return [today - timedelta(days=i) for i in range(WINDOW_DAYS - 1, -1, -1)]


def collect(today: date | None = None, records: list[dict] | None = None, observed: bool = True) -> dict:
    """Records -> the metric. `observed` False (no file yet) leaves every cell unknown."""
    today = today or date.today()
    counts = evening_counts(resolve(records or []))
    win = window(today)

    # None = before tracking, today's still-open evening, or nothing observed.
    cell_counts: list[int | None] = []
    for d in win:
        known = observed and TRACKING_SINCE <= d < today
        cell_counts.append(counts.get(d, 0) if known else None)

    # `days` are bools (dry = hit) because the renderer scores cells that way;
    # the true counts stay in `counts`.
    days = [None if n is None else n == 0 for n in cell_counts]
    total = sum(counts.get(d, 0) for d in win if d >= TRACKING_SINCE)
    weeknights_over = sum(
        1 for d in win
        if d >= TRACKING_SINCE and d.weekday() <= 3 and counts.get(d, 0) > WEEKNIGHT_MAX
    )

    metric: dict = {"days": days, "counts": cell_counts, "total": total,
                    "weeknights_over": weeknights_over}
    if not any(c is not None for c in days):
        metric["value"] = None
        metric["note"] = "not answered yet"
    else:
        metric["value"] = sum(1 for c in days if c)
        metric["note"] = f"{total} drink{'s' if total != 1 else ''}"

    return {"written_at": datetime.now().astimezone().isoformat(), "metrics": {"drinks": metric}}


def write_state(blob: dict, path: Path = STATE) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob, indent=2))
    tmp.replace(path)
    return blob


def run(
    today: date | None = None,
    drinks_path: Path = DRINKS,
    raw_copy_path: Path = RAW_COPY,
    state_path: Path = STATE,
    quiet: bool = False,
) -> int:
    """Read, copy, render state. Non-zero and untouched on a degraded read."""
    today = today or date.today()
    records, status = read_drinks(drinks_path)

    if status == DEGRADED:
        if not quiet:
            print(f"  ! drinks: {drinks_path.name} unreadable — keeping the prior value")
        return 1

    if status == OK:
        try:
            raw_copy_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(drinks_path, raw_copy_path)
        except OSError:
            pass  # a backup; losing it is not a failure

    blob = collect(today=today, records=records, observed=status == OK)
    write_state(blob, state_path)

    if not quiet:
        m = blob["metrics"]["drinks"]
        cells = "".join("·" if c is None else ("■" if c else "□") for c in m["days"])
        val = "—" if m["value"] is None else f"{m['value']} dry"
        source = "no app data yet" if status == ABSENT else f"{len(resolve(records))} drinks logged"
        print(f"wrote {state_path} (drinks={val} {cells} {m['note']}) [{source}]")
    return 0


def main() -> int:
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
