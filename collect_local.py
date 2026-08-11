#!/usr/bin/env python3
"""Local collectors for `habits` — the metrics computable from disk, no API keys.

Writes ~/.local/state/habits/local.json. One file per writer: the MCP-backed
metrics land in mcp.json, so two refreshes can never clobber each other.

Journaling, per Alex (2026-08-11):
    "craft daily note with paragraph text (not just tasks) OR voice memo = journal"
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

VAULT = Path.home() / "Obsidian" / "alexpriest"
CRAFT_DAILY = VAULT / "Craft" / "Daily Notes"
SITE_WRITING = VAULT / "Site" / "Writing"
STATE = Path.home() / ".local" / "state" / "habits" / "local.json"

# A voice memo transcript, written by the voice-memo-transcribe pipeline.
VOICE_MEMO = re.compile(r"^\s*\+?\s*#{1,4}\s*Voice memo:", re.M)
# Agent-appended content sits below a divider; the Craft mirror renders it ***.
DIVIDER = re.compile(r"^\s*(?:\*\*\*|---|___)\s*$")
# A calendar event block: `1:00 PM` - `2:00 PM` - Alex/John jam
CAL_EVENT = re.compile(r"^\s*`\d{1,2}:\d{2}\s*[AP]M`")
TASK = re.compile(r"^\s*[-*+]\s*\[[ xX~]\]")
BULLET = re.compile(r"^\s*[-*+]\s+")
HEADING = re.compile(r"^\s*\+?\s*#{1,6}\s")
# Calendar-injected boilerplate that is not Alex writing.
NOISE = re.compile(
    r"(Event Notes|Notion Calendar|meet\.google\.com|support\.google\.com"
    r"|Join with Google Meet|blockedEvent|zoom\.us)",
    re.I,
)
# Agent-written prose signatures. Agents lean on Obsidian callouts and on
# bold-lead paragraphs; Alex almost never does either in a daily note.
# ⚠️ KNOWN LIMIT: this does not catch plain agent prose. Verified 2026-08-11 on
# the 8/06 note (an agent-written wagyu cooking guide), which still scores as
# journaled. A machine writes good paragraphs, and no text heuristic separates
# them from Alex's. The metric is a WEEKLY COUNT against a threshold of 4, so a
# one-day false positive rarely flips the verdict — but it ships `provisional`
# and `habits journal` prints the day-by-day so a wrong day is visible.
CALLOUT = re.compile(r"^\s*>")
BOLD_LEAD = re.compile(r"^\s*\*\*[^*]+\*\*")
MIN_PROSE_CHARS = 80


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text[end + 4 :] if end != -1 else text


def above_divider(text: str) -> str:
    """Everything before the first divider is Alex's; below it is agent-appended."""
    out = []
    for line in text.splitlines():
        if DIVIDER.match(line):
            break
        out.append(line)
    return "\n".join(out)


def journaled(text: str) -> tuple[bool, str]:
    """Did Alex journal in this note? Returns (verdict, why)."""
    body = strip_frontmatter(text)
    if VOICE_MEMO.search(body):
        return True, "voice memo"

    prose_chars = 0
    for raw in above_divider(body).splitlines():
        line = raw.strip()
        if not line:
            continue
        if CAL_EVENT.match(line) or TASK.match(line) or HEADING.match(line):
            continue
        if NOISE.search(line):
            continue
        if BULLET.match(line):
            continue
        if CALLOUT.match(line) or BOLD_LEAD.match(line):
            continue
        prose_chars += len(line)

    if prose_chars >= MIN_PROSE_CHARS:
        return True, f"{prose_chars} chars of prose"
    return False, f"only {prose_chars} chars" if prose_chars else "tasks/events only"


def note_path(d: date) -> Path:
    return CRAFT_DAILY / f"{d:%Y}" / f"{d:%m-%B}" / f"{d:%Y-%m-%d}.md"


def journal_days(days: list[date]) -> list[tuple[date, bool, str]]:
    rows = []
    for d in days:
        p = note_path(d)
        if not p.exists():
            rows.append((d, False, "no note"))
            continue
        try:
            verdict, why = journaled(p.read_text(errors="ignore"))
        except OSError as e:
            rows.append((d, False, f"unreadable: {e}"))
            continue
        rows.append((d, verdict, why))
    return rows


def days_since_publish() -> tuple[int | None, str]:
    """Days since anything went live on alexpriest.com."""
    newest: tuple[date, str] | None = None
    if not SITE_WRITING.exists():
        return None, ""
    for f in SITE_WRITING.glob("*.md"):
        head = f.read_text(errors="ignore")[:1200]
        status = re.search(r"^status:\s*(\S+)", head, re.M)
        if not status or status.group(1).strip("'\" ") != "published":
            continue
        stamp = re.search(r"^(?:date|created):\s*'?\[?\[?(\d{4}-\d{2}-\d{2})", head, re.M)
        if not stamp:
            continue
        d = date.fromisoformat(stamp.group(1))
        if newest is None or d > newest[0]:
            newest = (d, f.stem)
    if newest is None:
        return None, ""
    return (date.today() - newest[0]).days, f'last: "{newest[1]}", {newest[0]:%b %-d}'


def this_week(today: date | None = None) -> list[date]:
    today = today or date.today()
    start = today - timedelta(days=today.weekday())
    return [start + timedelta(days=i) for i in range(7) if start + timedelta(days=i) <= today]


def journal_history(weeks: int = 8, today: date | None = None) -> list[int | None]:
    """Journaled-days per week for the N *completed* weeks before this one.

    Fully reconstructible from the Craft mirror, so the sparkline is real on day
    one instead of filling in over two months.

    ⚠️ A week with NO note files at all is `None` (unknown), never 0. The Craft
    mirror only reaches back to 2026-07-16; scoring the empty weeks before it as
    zero drew five weeks of floor blocks that read as "you journaled nothing"
    when the truth was "nothing was mirrored yet."
    """
    today = today or date.today()
    this_monday = today - timedelta(days=today.weekday())
    out: list[int | None] = []
    for back in range(weeks, 0, -1):
        start = this_monday - timedelta(weeks=back)
        days = [start + timedelta(days=i) for i in range(7)]
        rows = journal_days(days)
        # A week the mirror only partly covers under-counts for a reason that has
        # nothing to do with Alex — the week of Jul 13 has notes for 4 of 7 days
        # (the mirror starts Jul 16) and scored 0. Majority-missing is unknown.
        if sum(1 for _, _, why in rows if why == "no note") > len(rows) / 2:
            out.append(None)
            continue
        out.append(sum(1 for _, ok, _ in rows if ok))
    return out


def collect() -> dict:
    rows = journal_days(this_week())
    hit = sum(1 for _, ok, _ in rows if ok)
    since, note = days_since_publish()

    metrics = {
        "journal": {
            "value": hit,
            "note": "",
            "provisional": True,
            "history": journal_history(),
            "detail": [{"date": str(d), "journaled": ok, "why": why} for d, ok, why in rows],
        }
    }
    if since is not None:
        metrics["writing"] = {"value": since, "note": note}

    return {"written_at": datetime.now().astimezone().isoformat(), "metrics": metrics}


def main() -> int:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    blob = collect()
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob, indent=2))
    tmp.replace(STATE)
    print(f"wrote {STATE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
