#!/usr/bin/env python3
"""Vendor-API collectors for `habits` — lifts (Hevy), sleep (Oura), rides (Strava).

Writes state/api.json.

Credentials resolve env-first, then 1Password. Nothing is ever read from a
command line, so a secret cannot end up in shell history or a process listing.

    HABITS_HEVY_API_KEY      or  op://Claude/Hevy API/credential
    HABITS_OURA_TOKEN        or  op://Claude/Oura API/credential
    HABITS_STRAVA_CLIENT_ID  or  op://Claude/Strava API/client_id
    HABITS_STRAVA_SECRET     or  op://Claude/Strava API/client_secret
    HABITS_STRAVA_REFRESH    or  op://Claude/Strava API/refresh_token

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
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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
    val = os.environ.get(env_var)
    if val:
        return val.strip()
    try:
        proc = subprocess.run(
            ["op", "read", op_ref], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise SourceError(f"{env_var} unset and 1Password unavailable ({e})") from e
    if proc.returncode != 0:
        raise SourceError(f"{env_var} unset and `op read {op_ref}` failed")
    return proc.stdout.strip()


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
    token = secret("HABITS_OURA_TOKEN", "op://Claude/Oura API/credential")
    start = today - timedelta(days=HISTORY_DAYS)
    payload = get_json(
        "https://api.ouraring.com/v2/usercollection/daily_sleep",
        {"Authorization": f"Bearer {token}"},
        {"start_date": str(start), "end_date": str(today)},
    )
    # daily_sleep carries the score; the durations live on the sleep periods.
    periods = get_json(
        "https://api.ouraring.com/v2/usercollection/sleep",
        {"Authorization": f"Bearer {token}"},
        {"start_date": str(start), "end_date": str(today)},
    )
    by_day: dict[date, int] = {}
    for p in periods.get("data") or []:
        if p.get("type") in ("deleted", "rest"):
            continue
        day = p.get("day")
        secs = p.get("total_sleep_duration")
        if not day or not secs:
            continue
        d = date.fromisoformat(day)
        # Naps add to the day's total; Oura splits a broken night into periods.
        by_day[d] = by_day.get(d, 0) + int(secs)

    win = window_days(today)
    nightly = [by_day.get(d) for d in win]
    known = [v for v in nightly if v]
    if not known:
        raise SourceError("no sleep periods in the window")

    series = [by_day.get(d) for d in window_days(today, HISTORY_DAYS)]
    return {
        "value": round(sum(known) / len(known) / 3600, 2),
        "history": [None if v is None else round(v / 3600, 2) for v in series],
        "note": "" if len(known) == WINDOW_DAYS else f"{len(known)}/{WINDOW_DAYS} nights",
    }


# ----------------------------------------------------------------- Strava


def strava_rides(today: date) -> dict:
    client_id = secret("HABITS_STRAVA_CLIENT_ID", "op://Claude/Strava API/client_id")
    client_secret = secret("HABITS_STRAVA_SECRET", "op://Claude/Strava API/client_secret")
    refresh = secret("HABITS_STRAVA_REFRESH", "op://Claude/Strava API/refresh_token")

    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        }
    ).encode()
    req = urllib.request.Request(
        "https://www.strava.com/oauth/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            token = json.loads(resp.read().decode())["access_token"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError) as e:
        raise SourceError(f"strava token refresh failed ({type(e).__name__})") from e

    after = datetime.combine(
        today - timedelta(days=FETCH_DAYS), datetime.min.time(), timezone.utc
    )
    activities = get_json(
        "https://www.strava.com/api/v3/athlete/activities",
        {"Authorization": f"Bearer {token}"},
        {"after": int(after.timestamp()), "per_page": 100},
    )

    hit: set[date] = set()
    for a in activities:
        sport = a.get("sport_type") or a.get("type")
        if sport not in RIDE_TYPES:
            continue
        if (a.get("moving_time") or 0) < MIN_RIDE_MINUTES * 60:
            continue
        stamp = a.get("start_date_local") or a.get("start_date")
        if not stamp:
            continue
        hit.add(datetime.fromisoformat(stamp.replace("Z", "+00:00")).date())

    out = count_metric(hit, today)
    out["note"] = f"≥{MIN_RIDE_MINUTES}min, no e-bike"
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
