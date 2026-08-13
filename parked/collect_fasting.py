#!/usr/bin/env python3
"""Eating-window collector for `habits` — the 18:6, Mon–Fri.

Writes state/fasting.json. The log of record is a jsonl file in the vault, NOT in
state/: state/ is gitignored scratch that a rebuild can throw away, and this is
the one metric here whose history cannot be reconstructed from anything else.

    ~/Obsidian/alexpriest/Claude/Coach/Data/eating-window.jsonl

ONE LOG, TWO WRITERS
--------------------
Alex tracks his fasts in **Zero**, so Zero is the system of record and this file
never asks him to re-enter what Zero already knows. Every record carries a
`source`, and two things write here:

  * `zero-export` — `import_zero.py`, fed by Zero's "Download My Data" export.
    This is ground truth and it backfills the entire history in one go.
  * `sunday-text` — the weekly check-in, which covers the days since the last
    export so the current week is never blank.

A later record for a day supersedes an earlier one, so a Zero import silently
corrects anything the weekly answer got wrong.

WHY THE EXPORT AND NOT AN API — checked 2026-08-13, do not re-research
---------------------------------------------------------------------
  * Zero publishes no API. Its help centre — all 42 articles, enumerated — has
    no developer access, no OAuth, no webhooks. Its backend hosts resolve and
    404 at the root; nothing public documents a path.
  * The ONLY supported read path is in-app "Download My Data": it emails a link
    plus a password for a zip of JSON. Capped at one request per 24h, delivered
    within 48h. That makes it a periodic backfill, never a live feed.
  * Zero writes only WEIGHT to Apple Health, never fast sessions — and HealthKit
    has no fasting type to write them as (checked: not among the 122 quantity
    identifiers, not among the category identifiers). There is also no Health
    app on macOS, so no Health-based path reaches this machine anyway.

THE PROTOCOL SHAPES THE MATH
----------------------------
18:6 runs MONDAY–FRIDAY only; Saturday and Sunday are deliberately off (long
ride, family). So the denominator is the 5 weekdays in the window, never 7, and
a weekend cell is NOT a miss — it renders as unknown. Scoring Saturday as a
failure would be scoring him against a rule he does not have.

THE PROTOCOL SHAPES THE MATH
----------------------------
18:6 runs MONDAY–FRIDAY only; Saturday and Sunday are deliberately off (long
ride, family). So the denominator is the 5 weekdays in the window, never 7, and
a weekend cell is NOT a miss — it renders as unknown. Scoring Saturday as a
failure would be scoring him against a rule he does not have.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path.home()
LOG = HOME / "Obsidian" / "alexpriest" / "Claude" / "Coach" / "Data" / "eating-window.jsonl"
STATE = HOME / "Code" / "tools" / "habits" / "state" / "fasting.json"

WINDOW_DAYS = 7
WEEKDAYS = {0, 1, 2, 3, 4}  # Monday..Friday — the protocol's actual span
WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def read_log() -> dict[date, dict]:
    """date -> record. Last write for a day wins, so a correction just appends."""
    out: dict[date, dict] = {}
    if not LOG.exists():
        return out
    for line in LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            out[date.fromisoformat(rec["date"])] = rec
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return out


def append(records: list[dict]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def record(days: dict[date, bool], source: str = "manual") -> list[dict]:
    stamp = datetime.now().astimezone().isoformat()
    recs = [
        {"date": str(d), "held": bool(held), "source": source, "logged_at": stamp}
        for d, held in sorted(days.items())
    ]
    append(recs)
    return recs


def window(today: date) -> list[date]:
    return [today - timedelta(days=i) for i in range(WINDOW_DAYS - 1, -1, -1)]


def collect(today: date | None = None) -> dict:
    today = today or date.today()
    log = read_log()
    win = window(today)

    # None = not applicable (weekend) or not yet answered. Never False by default:
    # an unanswered Tuesday is not a broken Tuesday.
    cells: list[bool | None] = []
    for d in win:
        if d.weekday() not in WEEKDAYS:
            cells.append(None)
        elif d in log:
            cells.append(bool(log[d]["held"]))
        else:
            cells.append(None)

    answered = [c for c in cells if c is not None]
    weekdays_in_window = sum(1 for d in win if d.weekday() in WEEKDAYS)

    metric: dict = {"days": cells, "unit_denominator": weekdays_in_window}
    if not answered:
        metric["value"] = None
        metric["note"] = "not answered yet"
    else:
        metric["value"] = sum(1 for c in answered if c)
        missing = weekdays_in_window - len(answered)
        metric["note"] = f"{missing} day{'s' if missing != 1 else ''} unanswered" if missing else ""

    return {"written_at": datetime.now().astimezone().isoformat(), "metrics": {"fasting": metric}}


def write_state(today: date | None = None) -> dict:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    blob = collect(today)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob, indent=2))
    tmp.replace(STATE)
    return blob


def parse_week(spec: str, today: date | None = None) -> dict[date, bool]:
    """'mon,tue,thu' -> that Mon–Fri marked held, the rest missed.

    This is the shape a one-text answer takes: he replies with the days he held
    and everything else in the working week is a miss. 'all' and 'none' both work.
    """
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    spec = spec.strip().lower()
    if spec in ("all", "every", "7", "5"):
        held = set(range(5))
    elif spec in ("none", "0"):
        held = set()
    else:
        held = set()
        for token in spec.replace(" ", "").split(","):
            if not token:
                continue
            key = token[:3]
            if key not in WEEKDAY_NAMES[:5]:
                raise ValueError(f"not a weekday: {token}")
            held.add(WEEKDAY_NAMES.index(key))
    # Never log a day that has not happened yet.
    return {
        monday + timedelta(days=i): (i in held)
        for i in range(5)
        if monday + timedelta(days=i) <= today
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Eating-window collector and logger.")
    sub = ap.add_subparsers(dest="cmd")

    rec = sub.add_parser("record", help="log whether the window held")
    rec.add_argument("verdict", nargs="?", choices=["yes", "no"], help="for a single day")
    rec.add_argument("--date", help="YYYY-MM-DD (default today)")
    rec.add_argument("--week", help="days held this week, e.g. 'mon,tue,fri' | all | none")
    rec.add_argument("--source", default="manual")

    sub.add_parser("show", help="print the current window")
    args = ap.parse_args()

    if args.cmd == "record":
        if args.week:
            days = parse_week(args.week)
        elif args.verdict:
            d = date.fromisoformat(args.date) if args.date else date.today()
            days = {d: args.verdict == "yes"}
        else:
            ap.error("give a verdict (yes/no) or --week")
        written = record(days, args.source)
        for r in written:
            print(f"  {r['date']}  {'held' if r['held'] else 'broke'}")

    blob = write_state()
    m = blob["metrics"]["fasting"]
    cells = "".join("·" if c is None else ("█" if c else "░") for c in m["days"])
    val = "—" if m["value"] is None else f"{m['value']}/{m['unit_denominator']}"
    print(f"wrote {STATE} (fasting={val} {cells} {m.get('note') or ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
