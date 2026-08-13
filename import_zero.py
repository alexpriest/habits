#!/usr/bin/env python3
"""Import a Zero "Download My Data" export into the eating-window log.

    python3 import_zero.py ~/Downloads/zero-export.zip [--password PW]
    python3 import_zero.py ~/Downloads/zero-export/           # already unzipped
    python3 import_zero.py <path> --dry-run                   # look before writing

Zero has no API (see collect_fasting.py for the full 2026-08-13 finding), so this
export is the only supported way to get real fast data onto this machine. Alex
taps "Download My Data" in the app; Zero emails a link and a password; this reads
what comes out and appends to the vault log with source `zero-export`.

⚠️ THE SCHEMA IS NOT PUBLISHED AND THIS PARSER HAS NOT YET SEEN A REAL EXPORT.
Zero ships JSON now (the CSV era ended years ago) and no public sample of the
current shape exists. So this deliberately DISCOVERS rather than assumes: it
looks for records carrying a start and an end timestamp under any of the common
spellings, and if it cannot find them it prints the structure it did find and
exits 2. It will never guess a mapping and silently write wrong history — a
fasting log that is quietly wrong is worse than one that is empty, because
nothing downstream can tell.

Fixing a failed run is a five-minute job: read the printed structure, add the
real key names to START_KEYS / END_KEYS, re-run.

HOW A FAST BECOMES A VERDICT
----------------------------
The protocol is 18:6, eating 4:00pm–10:00pm, Monday–Friday. So a fast that
"held" for weekday D is one that ENDED on D at or after the window opened (he
did not eat early) and ran at least MIN_FAST_HOURS. Tolerances are generous on
purpose — this is a habit tracker, not a stopwatch, and calling a 15:52 break
a failure would make the number annoying rather than useful.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_fasting import WEEKDAYS, record, write_state  # noqa: E402

START_KEYS = ("start", "startdate", "start_date", "starttime", "start_time", "began", "begin")
END_KEYS = ("end", "enddate", "end_date", "endtime", "end_time", "ended", "finish")

# Window opens 16:00. Allow a little early without calling it a break.
WINDOW_OPENS = time(16, 0)
GRACE_MINUTES = 15
MIN_FAST_HOURS = 17.0


def parse_stamp(val) -> datetime | None:
    if isinstance(val, (int, float)):
        # Epoch seconds or milliseconds.
        secs = val / 1000 if val > 1e11 else val
        try:
            return datetime.fromtimestamp(secs).astimezone()
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(val, str) or not val.strip():
        return None
    text = val.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%y %H:%M", "%m/%d/%Y %H:%M"):
        try:
            dt = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt.astimezone() if dt.tzinfo else dt
    return None


def find_keys(obj: dict) -> tuple[str, str] | None:
    lower = {k.lower().replace(" ", "").replace("-", ""): k for k in obj}
    start = next((lower[k] for k in START_KEYS if k in lower), None)
    end = next((lower[k] for k in END_KEYS if k in lower), None)
    return (start, end) if start and end else None


def walk_records(node, found: list[dict]) -> None:
    """Zero could nest fasts under any wrapper, so search the whole tree."""
    if isinstance(node, dict):
        if find_keys(node):
            found.append(node)
            return
        for v in node.values():
            walk_records(v, found)
    elif isinstance(node, list):
        for v in node:
            walk_records(v, found)


def load_any(path: Path) -> list[dict]:
    out: list[dict] = []
    if path.suffix.lower() == ".json":
        try:
            walk_records(json.loads(path.read_text(errors="ignore")), out)
        except json.JSONDecodeError:
            pass
    elif path.suffix.lower() == ".csv":
        try:
            rows = list(csv.DictReader(path.read_text(errors="ignore").splitlines()))
        except (OSError, csv.Error):
            return out
        # The legacy CSV is Date,Start,End,Hours,Night Eating — Start/End are
        # clock times against Date, not timestamps.
        for row in rows:
            if row.get("Date") and row.get("Start") and row.get("End"):
                out.append({"__csv__": True, **row})
            elif find_keys(row):
                out.append(row)
    return out


def csv_to_span(row: dict) -> tuple[datetime, datetime] | None:
    day = parse_stamp(row.get("Date"))
    if not day:
        return None
    try:
        s_h, s_m = (int(x) for x in row["Start"].split(":")[:2])
        e_h, e_m = (int(x) for x in row["End"].split(":")[:2])
    except (ValueError, KeyError):
        return None
    start = day.replace(hour=s_h, minute=s_m)
    end = day.replace(hour=e_h, minute=e_m)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def spans(records: list[dict]) -> list[tuple[datetime, datetime]]:
    out = []
    for rec in records:
        if rec.get("__csv__"):
            span = csv_to_span(rec)
            if span:
                out.append(span)
            continue
        keys = find_keys(rec)
        if not keys:
            continue
        start, end = parse_stamp(rec[keys[0]]), parse_stamp(rec[keys[1]])
        if start and end and end > start:
            out.append((start, end))
    return out


def verdicts(fasts: list[tuple[datetime, datetime]]) -> dict[date, bool]:
    """A weekday is held when a long-enough fast ended at or after the window opened.

    Weekdays with fast data but no qualifying fast are False. Weekdays with NO
    fast data at all are left out entirely — absent, not failed. Weekends are
    never recorded: the protocol does not cover them.
    """
    seen: set[date] = set()
    held: set[date] = set()
    for start, end in fasts:
        day = end.date()
        if day.weekday() not in WEEKDAYS:
            continue
        seen.add(day)
        hours = (end - start).total_seconds() / 3600
        opened = datetime.combine(day, WINDOW_OPENS, tzinfo=end.tzinfo) - timedelta(
            minutes=GRACE_MINUTES
        )
        if hours >= MIN_FAST_HOURS and end >= opened:
            held.add(day)
    return {d: d in held for d in sorted(seen)}


def describe(root: Path) -> str:
    """What we actually found, so a failed run is fixable in one pass."""
    lines = ["", "  No fast records recognised. Structure found:", ""]
    for f in sorted(root.rglob("*"))[:40]:
        if f.is_dir() or f.suffix.lower() not in (".json", ".csv"):
            continue
        lines.append(f"  {f.relative_to(root)}  ({f.stat().st_size} B)")
        try:
            if f.suffix.lower() == ".json":
                blob = json.loads(f.read_text(errors="ignore"))
                if isinstance(blob, dict):
                    lines.append(f"      top-level keys: {sorted(blob)[:14]}")
                    for k, v in list(blob.items())[:6]:
                        if isinstance(v, list) and v and isinstance(v[0], dict):
                            lines.append(f"      {k}[0] keys: {sorted(v[0])[:14]}")
                elif isinstance(blob, list) and blob and isinstance(blob[0], dict):
                    lines.append(f"      [0] keys: {sorted(blob[0])[:14]}")
            else:
                head = f.read_text(errors="ignore").splitlines()[:1]
                lines.append(f"      header: {head}")
        except (json.JSONDecodeError, OSError, IndexError) as e:
            lines.append(f"      unreadable: {type(e).__name__}: {e}")
    lines += [
        "",
        "  Add the real start/end key names to START_KEYS / END_KEYS in this file.",
        "",
    ]
    return "\n".join(lines)


def extract(src: Path, password: str | None, workdir: Path) -> Path:
    if src.is_dir():
        return src
    try:
        with zipfile.ZipFile(src) as zf:
            zf.extractall(workdir, pwd=password.encode() if password else None)
        return workdir
    except RuntimeError as e:
        if "password" in str(e).lower():
            # AES-encrypted zips (WinZip AES) are beyond the stdlib. ditto handles them.
            proc = subprocess.run(
                ["ditto", "-x", "-k", str(src), str(workdir)],
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                return workdir
            raise SystemExit(
                f"habits: could not open {src.name} — wrong or missing --password"
            ) from e
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description="Import a Zero data export.")
    ap.add_argument("path", type=Path, help="the .zip Zero emailed, or an unzipped dir")
    ap.add_argument("--password", help="password from Zero's 'Export Data Request' email")
    ap.add_argument("--dry-run", action="store_true", help="show what would be logged")
    args = ap.parse_args()

    if not args.path.exists():
        raise SystemExit(f"habits: no such path: {args.path}")

    with tempfile.TemporaryDirectory() as tmp:
        root = extract(args.path, args.password, Path(tmp))
        records: list[dict] = []
        for f in sorted(root.rglob("*")):
            if f.is_file():
                records.extend(load_any(f))

        fasts = spans(records)
        if not fasts:
            print(describe(root))
            return 2

        days = verdicts(fasts)

    if not days:
        print(f"habits: {len(fasts)} fasts found, but none ended on a weekday — nothing to log")
        return 2

    tally = Counter(days.values())
    lo, hi = min(days), max(days)
    print(f"  {len(fasts)} fasts -> {len(days)} weekdays, {lo} to {hi}")
    print(f"  held {tally[True]}, broke {tally[False]}")

    if args.dry_run:
        for d, ok in sorted(days.items())[-14:]:
            print(f"    {d}  {'held' if ok else 'broke'}")
        print("\n  --dry-run: nothing written")
        return 0

    record(days, source="zero-export")
    write_state()
    print(f"  wrote {len(days)} records to the eating-window log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
