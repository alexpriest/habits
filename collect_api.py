#!/usr/bin/env python3
"""Vendor-API collectors for `habits` — lifts (Hevy), sleep (Oura), rides (Strava).

Writes state/api.json.

Credentials resolve env-first, then 1Password. Nothing is ever read from a
command line, so a secret cannot end up in shell history or a process listing.

    lifts   Hevy REST          op://Claude/Hevy API/credential
    rides   strava-mcp on Railway, over MCP Streamable HTTP
                               op://Claude/Strava MCP/{credential,url}
    sleep   Oura REST          op://Claude/Oura MCP/credential  (personal access token)

Rides go through Alex's own deployed MCP server rather than the Strava API
directly, because the Strava refresh token lives on that server's Railway volume
and never touches this machine — the server already refreshes it, so there is
nothing here to expire. Oura's server speaks the OLDER HTTP+SSE MCP transport
(/sse + /message; /mcp 404s), so it uses the REST API and a personal access
token instead of a second transport implementation for a single caller.

Each source fails INDEPENDENTLY. A missing Oura token must not cost you the lift
count, and a source that could not be read is simply absent from the state file,
which the dashboard renders as `—`. It never renders as zero: "we didn't look" and
"you didn't do it" are different facts and the whole tool depends on not
confusing them.
"""

from __future__ import annotations

import json
import os
import subprocess
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_client import McpError, call_tool  # noqa: E402

STATE = Path.home() / "Code" / "tools" / "habits" / "state" / "api.json"

WINDOW_DAYS = 7
HISTORY_DAYS = 14
# Enough to compute a rolling-7 for each of the last HISTORY_DAYS days.
FETCH_DAYS = WINDOW_DAYS + HISTORY_DAYS + 1

# A ride is training. An e-bike run to preschool is transport, and counting it
# would let the metric be satisfied by the school run — which is the opposite of
# what the habit is for. EBikeRide is excluded deliberately.
RIDE_TYPES = {"Ride", "GravelRide", "MountainBikeRide", "VirtualRide", "Handcycle"}
MIN_RIDE_MINUTES = 30

TIMEOUT = 30


class SourceError(RuntimeError):
    """This one source is unavailable. The others still run."""


# ------------------------------------------------------------- credentials


def secret(env_var: str, op_ref: str) -> str:
    """env -> op -> op inside an interactive zsh.

    ⚠️ The third hop is not paranoia, it is the launchd case. OP_SERVICE_ACCOUNT_TOKEN
    is defined in ~/.zshrc, and .zshrc is read ONLY by interactive shells — not by
    `zsh -lc`, and certainly not by launchd, which runs no shell at all. Verified
    2026-08-13 from the refresh job's own log: every `op read` sat there and hit
    the 30-second timeout, because with no token `op` falls back to prompting a
    human who is not there. `zsh -ic` sources .zshrc and resolves in under a
    second from a clean environment.

    Timeouts are short on purpose. Five secrets × a 30s hang was a two-and-a-half
    minute job that looked like a network problem and was actually a missing
    environment variable.
    """
    val = os.environ.get(env_var)
    if val:
        return val.strip()

    attempts = (
        ["op", "read", op_ref],
        ["/bin/zsh", "-ic", f"op read {op_ref!r}"],
    )
    for argv in attempts:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=12)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    raise SourceError(f"{env_var} unset and `op read {op_ref}` did not resolve")


def get_json(url: str, headers: dict[str, str], params: dict | None = None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SourceError(f"{url.split('?')[0]} -> HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise SourceError(f"{url.split('?')[0]} -> {type(e).__name__}") from e


# -------------------------------------------------------------- windowing


def window_days(today: date, n: int = WINDOW_DAYS) -> list[date]:
    return [today - timedelta(days=i) for i in range(n - 1, -1, -1)]


def rolling_history(dates: set[date], today: date) -> list[int]:
    """The rolling-7 count sampled once per day — what `surging` compares against."""
    out = []
    for back in range(HISTORY_DAYS - 1, -1, -1):
        end = today - timedelta(days=back)
        start = end - timedelta(days=WINDOW_DAYS - 1)
        out.append(sum(1 for d in dates if start <= d <= end))
    return out


def count_metric(days_hit: set[date], today: date) -> dict:
    win = window_days(today)
    return {
        "value": sum(1 for d in win if d in days_hit),
        "days": [d in days_hit for d in win],
        "history": rolling_history(days_hit, today),
    }


# ------------------------------------------------------------------- Hevy


def hevy_lifts(today: date) -> dict:
    key = secret("HABITS_HEVY_API_KEY", "op://Claude/Hevy API/credential")
    hit: set[date] = set()
    horizon = today - timedelta(days=FETCH_DAYS)
    page = 1
    while page <= 5:
        payload = get_json(
            "https://api.hevyapp.com/v1/workouts",
            {"api-key": key, "Accept": "application/json"},
            {"page": page, "pageSize": 10},
        )
        workouts = payload.get("workouts") or []
        if not workouts:
            break
        oldest = None
        for w in workouts:
            stamp = w.get("start_time") or w.get("created_at")
            if not stamp:
                continue
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()
            oldest = when.date() if oldest is None else min(oldest, when.date())
            if when.date() >= horizon:
                hit.add(when.date())
        if oldest is not None and oldest < horizon:
            break
        page += 1
    return count_metric(hit, today)


# ------------------------------------------------------------------- Oura


def oura_sleep(today: date) -> dict:
    # The 1Password item is "Oura MCP", not "Oura API" — the latter never existed,
    # so this read failed silently for as long as the collector has run (verified
    # 2026-08-14: `op item list --vault Claude` has exactly one Oura entry).
    token = secret("HABITS_OURA_TOKEN", "op://Claude/Oura MCP/credential")
    start = today - timedelta(days=HISTORY_DAYS)
    periods = get_json(
        "https://api.ouraring.com/v2/usercollection/sleep",
        {"Authorization": f"Bearer {token}"},
        {"start_date": str(start), "end_date": str(today)},
    )
    by_day: dict[date, int] = {}
    for p in periods.get("data") or []:
        if p.get("type") in ("deleted", "rest"):
            continue
        day, secs = p.get("day"), p.get("total_sleep_duration")
        if not day or not secs:
            continue
        # Oura splits a broken night into periods; naps add to the day's total.
        by_day[date.fromisoformat(day)] = by_day.get(date.fromisoformat(day), 0) + int(secs)

    win = window_days(today)
    known = [by_day[d] for d in win if d in by_day]
    if not known:
        raise SourceError("no sleep periods in the window")

    series = [by_day.get(d) for d in window_days(today, HISTORY_DAYS)]
    return {
        "value": round(sum(known) / len(known) / 3600, 2),
        "history": [None if v is None else round(v / 3600, 2) for v in series],
        "note": "" if len(known) == WINDOW_DAYS else f"{len(known)}/{WINDOW_DAYS} nights",
    }


# ----------------------------------------------------------------- Strava


# The MCP server renders activities for humans, so this reads its output rather
# than JSON. Strict on purpose: if the format ever changes, the count drops and
# `note` says how many blocks failed to parse — it must never silently read 0.
ACTIVITY_HEAD = re.compile(r"^(?P<name>.+?)\s+\((?P<sport>\w+)\)\s*$")
ACTIVITY_DATE = re.compile(r"^\s*Date:\s*(\d{4}-\d{2}-\d{2})")
ACTIVITY_TIME = re.compile(r"^\s*Moving time:\s*(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?")


def parse_activities(text: str) -> tuple[list[dict], int]:
    """(activities, unparsed_block_count) from the MCP server's rendering."""
    blocks, current = [], None
    for raw in text.splitlines():
        head = ACTIVITY_HEAD.match(raw.strip()) if raw.strip() and not raw.startswith(" ") else None
        if head:
            if current:
                blocks.append(current)
            current = {"sport": head.group("sport"), "date": None, "seconds": None}
            continue
        if current is None:
            continue
        m = ACTIVITY_DATE.match(raw)
        if m:
            current["date"] = m.group(1)
        m = ACTIVITY_TIME.match(raw)
        if m and any(m.groups()):
            h, mi, s = (int(g or 0) for g in m.groups())
            current["seconds"] = h * 3600 + mi * 60 + s
    if current:
        blocks.append(current)

    good = [b for b in blocks if b["date"] and b["seconds"] is not None]
    return good, len(blocks) - len(good)


def strava_rides(today: date) -> dict:
    base = secret("HABITS_STRAVA_MCP_URL", "op://Claude/Strava MCP/url")
    token = secret("HABITS_STRAVA_MCP_TOKEN", "op://Claude/Strava MCP/credential")
    try:
        text = call_tool(
            base, token, "get-recent-activities",
            {"start_date": str(today - timedelta(days=FETCH_DAYS)), "per_page": 100},
        )
    except McpError as e:
        raise SourceError(str(e)) from e

    activities, unparsed = parse_activities(text)
    if not activities and unparsed:
        raise SourceError(f"could not parse any of {unparsed} activity blocks")

    hit = {
        date.fromisoformat(a["date"])
        for a in activities
        if a["sport"] in RIDE_TYPES and a["seconds"] >= MIN_RIDE_MINUTES * 60
    }
    out = count_metric(hit, today)
    note = f"\u2265{MIN_RIDE_MINUTES}min, no e-bike"
    if unparsed:
        note += f" ({unparsed} unreadable)"
    out["note"] = note
    return out


# --------------------------------------------------------------- assemble


COLLECTORS = {"lifts": hevy_lifts, "sleep": oura_sleep, "rides": strava_rides}


def collect(today: date | None = None) -> tuple[dict, list[str]]:
    today = today or date.today()
    metrics: dict[str, dict] = {}
    problems: list[str] = []
    for key, fn in COLLECTORS.items():
        try:
            metrics[key] = fn(today)
        except SourceError as e:
            problems.append(f"{key}: {e}")
        except Exception as e:  # noqa: BLE001 - one bad source must not kill the rest
            problems.append(f"{key}: unexpected {type(e).__name__}: {e}")
    return (
        {"written_at": datetime.now().astimezone().isoformat(), "metrics": metrics},
        problems,
    )


def main() -> int:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    blob, problems = collect()

    # Merge over the previous file rather than replacing it: if Oura is down this
    # run, yesterday's sleep should keep rendering WITH ITS AGE rather than
    # vanishing. Staleness is visible in the UI; a hole is not.
    if STATE.exists():
        try:
            prior = json.loads(STATE.read_text())
            merged = dict(prior.get("metrics") or {})
            for key, entry in blob["metrics"].items():
                entry["written_at"] = blob["written_at"]
                merged[key] = entry
            for key, entry in merged.items():
                entry.setdefault("written_at", prior.get("written_at"))
            blob["metrics"] = merged
        except (json.JSONDecodeError, OSError):
            pass

    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob, indent=2))
    tmp.replace(STATE)

    got = ", ".join(sorted(k for k in blob["metrics"]))
    print(f"wrote {STATE} ({got or 'nothing'})")
    for p in problems:
        print(f"  ! {p}")
    # Exit 0 even with problems: a partial refresh is a success. The dashboard
    # shows what is missing, and a nonzero exit would just make launchd noisy.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
