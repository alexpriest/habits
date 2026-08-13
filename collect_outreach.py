#!/usr/bin/env python3
"""Outreach collector for `habits` — conversations Alex STARTED, not messages sent.

Writes state/outreach.json.

The metric is deliberately narrow. "Messages sent" is a useless number: 265 in a
week, most of them to Miranda and to live group chats, and it would read as a hit
every week forever. What's worth building is the habit of reaching out to someone
you've drifted from — so a conversation only counts when Alex sent the first
message after `gap_days` of silence in that thread (default 14).

Verified against real data 2026-08-13: "messages sent" = 265/wk, "initiated
(any gap)" = 18/wk, "initiated after 14d" = 7/wk and the names it surfaces are
the right ones (Adam Pelavin after 49 days, Meghan Joyce after 13).

⚠️ SQLITE TRAP: `strftime('%s', ...)` returns TEXT, and SQLite sorts every
INTEGER below every TEXT — so `ts > strftime(...)` is silently FALSE for every
row and the query returns zero with no error. Always CAST to INTEGER. This cost
a full debugging round here and it is the same trap logged in the iMessage
forensics notes.
"""

from __future__ import annotations

import collections
import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path.home()
CHAT_DB = HOME / "Library" / "Messages" / "chat.db"
PHONE_LOOKUP = HOME / "Obsidian" / "alexpriest" / "People" / "_index" / "phone-lookup.json"
STATE = HOME / "Code" / "tools" / "habits" / "state" / "outreach.json"

GMAIL_ACCOUNT = "hello@alexpriest.com"
# Alex's own number: notes-to-self and the Kit notification thread, not outreach.
SELF_HANDLES = {"+12702871307", "hello@alexpriest.com", "alex.priest@gmail.com"}
# The agent personas text him from these. Talking to Kit is not outreach.
AGENT_HANDLES = {"kit@alexpriest.com", "paloma@alexpriest.com"}

DEFAULT_GAP_DAYS = 14
WINDOW_DAYS = 7
HISTORY_DAYS = 14
# Enough history to measure a gap that predates the window.
LOOKBACK_DAYS = 400

# The thread's originating sender is Alex — the test for "he started this".
ALEX_SENDER = re.compile(r"(hello@alexpriest\.com|alex\.priest@gmail\.com)", re.I)


# ---------------------------------------------------------------- iMessage


def load_phone_lookup() -> dict[str, str]:
    """Last-10-digits -> display name, for the day-by-day detail only."""
    if not PHONE_LOOKUP.exists():
        return {}
    try:
        raw = json.loads(PHONE_LOOKUP.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, val in raw.items():
        digits = re.sub(r"\D", "", str(key))
        if digits:
            out[digits[-10:]] = val if isinstance(val, str) else str(val)
    return out


def imessage_initiations(gap_days: int, lookback: int = LOOKBACK_DAYS) -> list[dict]:
    """Every conversation Alex re-opened, with the silence that preceded it.

    Returns one row per chat — a thread reopened twice in the window still
    represents one act of reaching out.
    """
    if not CHAT_DB.exists():
        raise FileNotFoundError(f"no chat.db at {CHAT_DB}")

    con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            SELECT c.ROWID, c.chat_identifier, c.display_name, m.is_from_me,
                   m.date / 1000000000 + 978307200 AS ts
            FROM message m
            JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
            JOIN chat c ON c.ROWID = cmj.chat_id
            WHERE m.date / 1000000000 + 978307200
                  > CAST(strftime('%s', 'now', ?) AS INTEGER)
            ORDER BY c.ROWID, ts
            """,
            (f"-{lookback} days",),
        ).fetchall()
    finally:
        con.close()

    by_chat: dict[int, list[tuple[int, int]]] = collections.defaultdict(list)
    meta: dict[int, tuple[str, str]] = {}
    for cid, ident, disp, mine, ts in rows:
        by_chat[cid].append((ts, mine))
        meta[cid] = (ident or "", disp or "")

    lookup = load_phone_lookup()
    cutoff = time.time() - WINDOW_DAYS * 86400
    out = []
    for cid, msgs in by_chat.items():
        ident, disp = meta[cid]
        if ident in SELF_HANDLES or ident in AGENT_HANDLES:
            continue
        prev = None
        for ts, mine in msgs:
            if mine and ts >= cutoff and (prev is None or ts - prev > gap_days * 86400):
                digits = re.sub(r"\D", "", ident)[-10:]
                out.append(
                    {
                        "who": disp or lookup.get(digits) or ident,
                        "date": str(date.fromtimestamp(ts)),
                        "gap_days": None if prev is None else int((ts - prev) / 86400),
                        "channel": "imessage",
                    }
                )
                break
            prev = ts
    return out


# ------------------------------------------------------------------- Gmail


def gmail_initiations(gap_days: int) -> list[dict]:
    """Email threads ALEX started. For email, starting a thread IS the reaching out.

    Two filters, and both are load-bearing:

    1. Server-side, drop mail addressed to Alex himself or to kit@ — that removes
       the automated hello@ -> hello@ alarms and the mail he writes to his own
       assistant, neither of which is outreach.
    2. Client-side, require the THREAD'S FIRST SENDER to be Alex. `in:sent` labels
       a whole thread if any single message in it was sent, so replying to a
       marketing blast pulls that blast into the results under ITS subject line.
       Verified 2026-08-13: an unfiltered week returned "Reminder: You have offers
       to redeem on Xbox!" and "Your tokens at Yoto USA are about to expire!" as
       outreach. The `from` field is the thread's originator, so testing it drops
       them — 7 threads became the 4 he actually started.

    Subject is NOT used to detect replies: a thread keeps its ORIGINAL subject, so
    "Re:" never appears on the thread even when Alex's own message is a reply, and
    a "Fwd:" from Alex to a new person is real outreach that a subject filter
    would have thrown away.

    ⚠️ KNOWN LIMIT: mail sent through Slashy authenticates as hello@ and is
    indistinguishable from mail Alex typed himself — acceptable, Slashy IS Alex
    composing. But Kit can also send AS hello@ when asked to (the 8/11 Q36.5
    strap email), and that is counted here as Alex. Agent mail from kit@ is not.
    """
    if not shutil.which("gog"):
        raise FileNotFoundError("gog not on PATH")

    exclude = sorted(SELF_HANDLES | {"kit@alexpriest.com"})
    query = f"in:sent newer_than:{WINDOW_DAYS}d " + " ".join(
        f"-to:{addr}" for addr in exclude if "@" in addr
    )
    env = {**os.environ, "GOG_ACCOUNT": GMAIL_ACCOUNT}
    proc = subprocess.run(
        ["gog", "gmail", "search", query, "--max", "100", "--json"],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gog exit {proc.returncode}: {proc.stderr.strip()[:200]}")

    payload = json.loads(proc.stdout or "{}")
    out = []
    for thread in payload.get("threads") or []:
        if not ALEX_SENDER.search(str(thread.get("from") or "")):
            continue
        subject = (thread.get("subject") or "").strip()
        stamp = (thread.get("date") or "")[:10]
        try:
            date.fromisoformat(stamp)
        except ValueError:
            continue
        out.append(
            {
                "who": subject[:60] or "(no subject)",
                "date": stamp,
                "gap_days": None,
                "channel": "email",
            }
        )
    return out


# ------------------------------------------------------------------ assemble


def collect(gap_days: int = DEFAULT_GAP_DAYS) -> dict:
    events: list[dict] = []
    problems: list[str] = []
    ok: list[str] = []

    for name, fn in (("imessage", imessage_initiations), ("gmail", gmail_initiations)):
        try:
            events.extend(fn(gap_days))
            ok.append(name)
        except Exception as e:  # noqa: BLE001 - a dead source must degrade visibly
            problems.append(f"{name} unreachable ({type(e).__name__})")

    # Both sources dead means we know nothing. Never render 0 for "we didn't look."
    if len(problems) == 2:
        return {
            "written_at": datetime.now().astimezone().isoformat(),
            "metrics": {"outreach": {"value": None, "note": "; ".join(problems),
                                     "sources_ok": []}},
        }

    today = date.today()
    window = [today - timedelta(days=i) for i in range(WINDOW_DAYS - 1, -1, -1)]
    by_day = collections.Counter(e["date"] for e in events)

    return {
        "written_at": datetime.now().astimezone().isoformat(),
        "metrics": {
            "outreach": {
                "value": len(events),
                "note": "; ".join(problems),
                "days": [by_day.get(str(d), 0) > 0 for d in window],
                "detail": sorted(events, key=lambda e: e["date"]),
                "gap_days": gap_days,
                "sources_ok": ok,
            }
        },
    }


def keeps_prior(new: dict, prior_path: Path) -> bool:
    """Would writing this REPLACE a better number with a worse one?

    ⚠️ Found the hard way 2026-08-13. The launchd refresh runs in a context with
    no Full Disk Access, so chat.db raises OperationalError there while the same
    code from a terminal reads it fine. The job cheerfully wrote outreach=4
    (email only) over the true 11, every two hours, and the dashboard would have
    quietly reported a third of the real number forever.

    A run that saw FEWER sources than the last one is not new information, it is
    a partial outage. Keep what we had and let the age marker tell the story.
    """
    if not prior_path.exists():
        return False
    try:
        prior = json.loads(prior_path.read_text()).get("metrics", {}).get("outreach") or {}
    except (json.JSONDecodeError, OSError):
        return False
    if prior.get("value") is None:
        return False
    return len(new.get("sources_ok") or []) < len(prior.get("sources_ok") or [])


def main() -> int:
    gap = int(os.environ.get("HABITS_OUTREACH_GAP_DAYS", DEFAULT_GAP_DAYS))
    STATE.parent.mkdir(parents=True, exist_ok=True)
    blob = collect(gap)

    if keeps_prior(blob["metrics"]["outreach"], STATE):
        lost = blob["metrics"]["outreach"].get("note") or "a source"
        print(f"kept prior {STATE} — this run was degraded ({lost})")
        return 0

    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob, indent=2))
    tmp.replace(STATE)
    entry = blob["metrics"]["outreach"]
    print(f"wrote {STATE} (outreach={entry['value']} {entry.get('note') or ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
