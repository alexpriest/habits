#!/usr/bin/env python3
"""Eating-window collector for `habits` — the 18:6, Mon–Fri.

Writes state/fasting.json. The log of record is a jsonl file in the vault, NOT in
state/: state/ is gitignored scratch that a rebuild can throw away, and this is
the one metric here whose history cannot be reconstructed from anything else.

    ~/Obsidian/alexpriest/Claude/Coach/Data/eating-window.jsonl

WHERE THE TRUTH COMES FROM — this changed 2026-08-15
----------------------------------------------------
**Window** (`~/Code/projects/window`) is Alex's own iOS fasting tracker, and it
is now the system of record. It replaces Zero, which this file used to name as
the source of truth and no longer is.

Window writes one line per completed **session** to its iCloud container:

    ~/Library/Mobile Documents/iCloud~com~alexpriest~window/Documents/fasts.jsonl
    {"ended":"2026-08-15T16:02:40-05:00","goal_hours":18,"id":"…","source":"app",
     "started":"2026-08-14T22:04:11-05:00"}

This collector turns those sessions into one verdict per DAY and appends them to
the vault log. **The app ships facts; the judgment lives here** — the 18h
threshold, the Mon–Fri denominator and what counts as "held" are protocol, his
protocol has moved once already, and moving it again must be an edit to this
file rather than a TestFlight build.

    A fast belongs to the calendar day it ENDED, in local time. 8pm Central is
    01:00 the next day in UTC, so attributing by UTC would file one evening fast
    per day under the wrong date.

WRITER PRECEDENCE — highest wins for a given date
-------------------------------------------------
    manual       Alex typed it at a terminal (`habits fast yes`). A human
                 explicitly overriding a derived number is the last word.
    app          session-derived, ground truth going forward
    zero-export  historical backfill only (`import_zero.py`)
    sunday-text  the weekly check-in, for days nothing else covers

This is a change: the loader used to be plain last-write-wins by line order, and
a Sunday text answer arriving after a session would silently bury it. An unknown
source ranks below all four rather than above them.

THE DATALESS-FILE TRAP
----------------------
This runs under launchd against an iCloud path. An evicted file is replaced by a
`.name.icloud` placeholder, and reading it can fail or block. **A degraded run
keeps the prior value and lets it age visibly — it never writes a number it did
not actually observe**, and it exits non-zero so `habits refresh` says so.

There is precedent for exactly this class of bug in exactly this tool: on
2026-08-13 the refresh silently wrote `outreach=4` over the true `11` for two
days because chat.db was unreadable under launchd.

"No file at all" is NOT degraded — that is the app not being installed yet, a
known-nothing, and the vault log still answers from whatever else wrote to it.

WHY AN APP AND NOT AN API — checked 2026-08-13, re-checked 2026-08-15
--------------------------------------------------------------------
  * Zero publishes no API. Its help centre — all 42 articles, enumerated — has
    no developer access, no OAuth, no webhooks.
  * HealthKit has no fasting type to write a fast as, for any app.
  * There is no Health app on macOS, so no Health-based path reaches this
    machine anyway.
  Do not re-research this.

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
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path.home()
LOG = HOME / "Obsidian" / "alexpriest" / "Claude" / "Coach" / "Data" / "eating-window.jsonl"
# A copy of the raw sessions, so the history survives iCloud being reset.
RAW_COPY = HOME / "Obsidian" / "alexpriest" / "Claude" / "Coach" / "Data" / "fasts.jsonl"
SESSIONS = (
    HOME / "Library" / "Mobile Documents" / "iCloud~com~alexpriest~window"
    / "Documents" / "fasts.jsonl"
)
STATE = HOME / "Code" / "tools" / "habits" / "state" / "fasting.json"

WINDOW_DAYS = 7
WEEKDAYS = {0, 1, 2, 3, 4}  # Monday..Friday — the protocol's actual span
WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Higher wins. An unknown source gets 0 and therefore never displaces a known one.
PRECEDENCE = {"manual": 4, "app": 3, "zero-export": 2, "sunday-text": 1}

OK = "ok"
ABSENT = "absent"
DEGRADED = "degraded"


# ---------------------------------------------------------------------------
# Reading the sessions Window wrote
# ---------------------------------------------------------------------------


def read_sessions(path: Path = SESSIONS) -> tuple[list[dict], str]:
    """Return (sessions, status). Status is OK / ABSENT / DEGRADED.

    DEGRADED is the important one: it means a file that should be readable is
    not, and the caller must change nothing rather than conclude he did not fast.
    """
    if not path.exists():
        # iCloud evicts by swapping the file for a dot-prefixed placeholder.
        placeholder = path.with_name(f".{path.name}.icloud")
        if placeholder.exists():
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
        if {"id", "started", "ended"} <= rec.keys():
            out.append(rec)
    return out, OK


def _parse(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


def derive_days(sessions: list[dict]) -> dict[date, dict]:
    """Sessions -> one verdict per day, keyed by the LOCAL day the fast ended.

    Window appends a superseding record carrying the same `id` when a fast is
    edited, so the last record for an id is the only one that counts. A day with
    two fasts is held if either of them reached its goal.
    """
    latest: dict[str, dict] = {}
    for rec in sessions:
        latest[str(rec["id"])] = rec

    days: dict[date, dict] = {}
    for rec in latest.values():
        started, ended = _parse(str(rec["started"])), _parse(str(rec["ended"]))
        if started is None or ended is None:
            continue
        goal_hours = float(rec.get("goal_hours") or 18)
        held = (ended - started).total_seconds() >= goal_hours * 3600
        day = ended.date()
        # Either fast reaching goal holds the day.
        if day in days and days[day]["held"]:
            continue
        days[day] = {"held": held, "goal_hours": goal_hours}
    return dict(sorted(days.items()))


# ---------------------------------------------------------------------------
# The vault log
# ---------------------------------------------------------------------------


def load_log(path: Path = LOG) -> dict[date, dict]:
    """date -> record, resolved by writer precedence, then by line order.

    Was plain last-write-wins, which let a Sunday text overwrite a session.
    """
    best: dict[date, tuple[int, int, dict]] = {}
    if not path.exists():
        return {}
    try:
        text = path.read_text()
    except OSError:
        return {}

    for order, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            day = date.fromisoformat(rec["date"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            continue
        rank = PRECEDENCE.get(str(rec.get("source", "")), 0)
        current = best.get(day)
        if current is None or (rank, order) >= (current[0], current[1]):
            best[day] = (rank, order, rec)
    return {day: rec for day, (_, _, rec) in sorted(best.items())}


def append(records: list[dict], path: Path = LOG) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def record(days: dict[date, bool], source: str = "manual", path: Path = LOG) -> list[dict]:
    stamp = datetime.now().astimezone().isoformat()
    recs = [
        {"date": str(d), "held": bool(held), "source": source, "logged_at": stamp}
        for d, held in sorted(days.items())
    ]
    append(recs, path)
    return recs


def sync_days(days: dict[date, dict], path: Path = LOG) -> list[dict]:
    """Append derived verdicts the log does not already hold from `app`.

    Idempotent: a re-run with unchanged sessions writes nothing, so the log does
    not grow by one line per refresh. An edit in the app changes the verdict and
    appends exactly one correction.

    Deliberately compares against the *app's own* last word, not the effective
    value. A `manual` record outranks this one and must not provoke a write war.
    """
    existing: dict[date, bool] = {}
    if path.exists():
        try:
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("source") == "app":
                        existing[date.fromisoformat(rec["date"])] = bool(rec["held"])
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    continue
        except OSError:
            return []

    stamp = datetime.now().astimezone().isoformat()
    pending = [
        {"date": str(day), "held": bool(v["held"]), "source": "app", "logged_at": stamp}
        for day, v in days.items()
        if existing.get(day) != bool(v["held"])
    ]
    if pending:
        append(pending, path)
    return pending


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


def window(today: date) -> list[date]:
    return [today - timedelta(days=i) for i in range(WINDOW_DAYS - 1, -1, -1)]


def collect(today: date | None = None, log: dict[date, dict] | None = None) -> dict:
    today = today or date.today()
    log = load_log() if log is None else log
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


def write_state(blob: dict, path: Path = STATE) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob, indent=2))
    tmp.replace(path)
    return blob


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def run(
    today: date | None = None,
    sessions_path: Path = SESSIONS,
    log_path: Path = LOG,
    raw_copy_path: Path = RAW_COPY,
    state_path: Path = STATE,
    quiet: bool = False,
) -> int:
    """Sync sessions into the log, then render state. Non-zero on a degraded read.

    On DEGRADED it returns before touching ANYTHING — no log append, no raw copy,
    and above all no state write. The previous state file stays exactly as it was
    and its `written_at` ages, which is how `habits` shows the number is stale
    instead of showing a wrong one.
    """
    today = today or date.today()
    sessions, status = read_sessions(sessions_path)

    if status == DEGRADED:
        if not quiet:
            print(f"  ! fasting: {sessions_path.name} unreadable — keeping the prior value")
        return 1

    if status == OK:
        sync_days(derive_days(sessions), log_path)
        try:
            raw_copy_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(sessions_path, raw_copy_path)
        except OSError:
            pass  # The copy is a belt-and-braces backup; losing it is not a failure.

    blob = collect(today=today, log=load_log(log_path))
    write_state(blob, state_path)

    if not quiet:
        m = blob["metrics"]["fasting"]
        cells = "".join("·" if c is None else ("█" if c else "░") for c in m["days"])
        val = "—" if m["value"] is None else f"{m['value']}/{m['unit_denominator']}"
        note = m.get("note") or ""
        source = "no app data yet" if status == ABSENT else f"{len(sessions)} sessions"
        print(f"wrote {state_path} (fasting={val} {cells} {note}) [{source}]")
    return 0


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
        for r in record(days, args.source):
            print(f"  {r['date']}  {'held' if r['held'] else 'broke'}")

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
