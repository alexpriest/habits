#!/usr/bin/env python3
"""Tests for the eating-window collector.

Run: python3 -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import collect_fasting as cf  # noqa: E402

CENTRAL = timezone(timedelta(hours=-5))


def session(started: str, ended: str, goal: float = 18, sid: str = "", source: str = "app") -> dict:
    return {
        "id": sid or f"{started}|{ended}",
        "started": started,
        "ended": ended,
        "goal_hours": goal,
        "source": source,
    }


def write_lines(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


class DeriveDays(unittest.TestCase):
    def test_a_fast_belongs_to_the_day_it_ended(self):
        # Started Thursday night, ended Friday afternoon: this is Friday's fast.
        days = cf.derive_days([session("2026-08-13T22:04:11-05:00", "2026-08-14T16:10:00-05:00")])

        self.assertEqual(list(days), [date(2026, 8, 14)])
        self.assertTrue(days[date(2026, 8, 14)]["held"])

    def test_the_threshold_is_not_generous(self):
        # The design spec's own illustrative line — 22:04:11 to 16:02:40 — is
        # 17h58m29s. It reads like a clean 18h fast and is not one. Nothing here
        # rounds in his favour.
        days = cf.derive_days([session("2026-08-14T22:04:11-05:00", "2026-08-15T16:02:40-05:00")])

        self.assertFalse(days[date(2026, 8, 15)]["held"])

    def test_the_ended_day_is_the_local_day_not_the_utc_one(self):
        # 8pm Central is 01:00 the next day in UTC. Attributing by UTC would file
        # this fast under the 16th.
        days = cf.derive_days([session("2026-08-15T02:00:00-05:00", "2026-08-15T20:00:00-05:00")])

        self.assertEqual(list(days), [date(2026, 8, 15)])

    def test_short_of_the_goal_is_recorded_as_not_held(self):
        days = cf.derive_days([session("2026-08-13T22:00:00-05:00", "2026-08-14T14:00:00-05:00")])

        self.assertFalse(days[date(2026, 8, 14)]["held"])

    def test_the_goal_is_the_one_that_session_carried(self):
        # He moved the goal to 16 for that fast; it must be scored against 16.
        days = cf.derive_days(
            [session("2026-08-13T22:00:00-05:00", "2026-08-14T14:00:00-05:00", goal=16)]
        )

        self.assertTrue(days[date(2026, 8, 14)]["held"])

    def test_a_later_record_for_the_same_id_supersedes_the_earlier_one(self):
        # The app appends a correction rather than rewriting; the last one is truth.
        days = cf.derive_days(
            [
                session("2026-08-13T22:00:00-05:00", "2026-08-14T14:00:00-05:00", sid="x"),
                session("2026-08-13T22:00:00-05:00", "2026-08-14T16:30:00-05:00", sid="x"),
            ]
        )

        self.assertEqual(len(days), 1)
        self.assertTrue(days[date(2026, 8, 14)]["held"])

    def test_two_fasts_ending_the_same_day_hold_it_if_either_reached_goal(self):
        days = cf.derive_days(
            [
                session("2026-08-14T06:00:00-05:00", "2026-08-14T09:00:00-05:00", sid="a"),
                session("2026-08-13T21:00:00-05:00", "2026-08-14T16:00:00-05:00", sid="b"),
            ]
        )

        self.assertTrue(days[date(2026, 8, 14)]["held"])


class WriterPrecedence(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = self.dir / "eating-window.jsonl"

    def test_a_sunday_text_cannot_overwrite_a_session(self):
        # The bug this precedence exists to stop: the weekly check-in lands last
        # by line order and, under plain last-write-wins, silently buries the app.
        write_lines(
            self.log,
            [
                {"date": "2026-08-14", "held": True, "source": "app", "logged_at": "x"},
                {"date": "2026-08-14", "held": False, "source": "sunday-text", "logged_at": "y"},
            ],
        )

        log = cf.load_log(self.log)

        self.assertTrue(log[date(2026, 8, 14)]["held"])
        self.assertEqual(log[date(2026, 8, 14)]["source"], "app")

    def test_an_app_record_beats_a_zero_export_whatever_the_order(self):
        write_lines(
            self.log,
            [
                {"date": "2026-08-14", "held": True, "source": "app", "logged_at": "x"},
                {"date": "2026-08-14", "held": False, "source": "zero-export", "logged_at": "y"},
            ],
        )

        self.assertTrue(cf.load_log(self.log)[date(2026, 8, 14)]["held"])

    def test_zero_export_still_beats_a_sunday_text(self):
        write_lines(
            self.log,
            [
                {"date": "2026-08-10", "held": False, "source": "sunday-text", "logged_at": "x"},
                {"date": "2026-08-10", "held": True, "source": "zero-export", "logged_at": "y"},
            ],
        )

        self.assertTrue(cf.load_log(self.log)[date(2026, 8, 10)]["held"])

    def test_alex_typing_it_himself_outranks_every_derived_source(self):
        write_lines(
            self.log,
            [
                {"date": "2026-08-14", "held": False, "source": "app", "logged_at": "x"},
                {"date": "2026-08-14", "held": True, "source": "manual", "logged_at": "y"},
            ],
        )

        self.assertTrue(cf.load_log(self.log)[date(2026, 8, 14)]["held"])

    def test_within_one_source_the_last_line_wins(self):
        write_lines(
            self.log,
            [
                {"date": "2026-08-14", "held": False, "source": "app", "logged_at": "x"},
                {"date": "2026-08-14", "held": True, "source": "app", "logged_at": "y"},
            ],
        )

        self.assertTrue(cf.load_log(self.log)[date(2026, 8, 14)]["held"])

    def test_an_unknown_source_never_outranks_a_known_one(self):
        write_lines(
            self.log,
            [
                {"date": "2026-08-14", "held": True, "source": "app", "logged_at": "x"},
                {"date": "2026-08-14", "held": False, "source": "who-knows", "logged_at": "y"},
            ],
        )

        self.assertTrue(cf.load_log(self.log)[date(2026, 8, 14)]["held"])

    def test_a_malformed_line_is_skipped_not_fatal(self):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_text(
            json.dumps({"date": "2026-08-14", "held": True, "source": "app"}) + "\n"
            + "{not json\n"
        )

        self.assertTrue(cf.load_log(self.log)[date(2026, 8, 14)]["held"])


class DegradedReads(unittest.TestCase):
    """An evicted iCloud file must never be read as 'he didn't fast'.

    Precedent: on 2026-08-13 this tool silently wrote outreach=4 over the true 11
    for two days because chat.db was unreadable under launchd.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.sessions = self.dir / "fasts.jsonl"

    def test_a_readable_file_is_ok(self):
        write_lines(self.sessions, [session("2026-08-13T22:00:00-05:00", "2026-08-14T16:00:00-05:00")])

        records, status = cf.read_sessions(self.sessions)

        self.assertEqual(status, cf.OK)
        self.assertEqual(len(records), 1)

    def test_no_file_at_all_is_absent_not_degraded(self):
        # The app simply isn't installed yet. That is a known-nothing, not a fault.
        records, status = cf.read_sessions(self.sessions)

        self.assertEqual(status, cf.ABSENT)
        self.assertEqual(records, [])

    def test_an_evicted_placeholder_is_degraded(self):
        # iCloud replaces an evicted file with `.name.icloud` and removes the real one.
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / ".fasts.jsonl.icloud").write_bytes(b"\x00placeholder")

        records, status = cf.read_sessions(self.sessions)

        self.assertEqual(status, cf.DEGRADED)
        self.assertEqual(records, [])

    def test_an_unreadable_file_is_degraded(self):
        write_lines(self.sessions, [session("2026-08-13T22:00:00-05:00", "2026-08-14T16:00:00-05:00")])
        self.sessions.chmod(0o000)
        try:
            records, status = cf.read_sessions(self.sessions)
        finally:
            self.sessions.chmod(0o644)

        self.assertEqual(status, cf.DEGRADED)

    def test_a_degraded_run_leaves_the_state_file_exactly_as_it_found_it(self):
        state = self.dir / "fasting.json"
        prior = {"written_at": "2026-08-14T09:00:00-05:00", "metrics": {"fasting": {"value": 4}}}
        state.write_text(json.dumps(prior))
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / ".fasts.jsonl.icloud").write_bytes(b"\x00placeholder")

        rc = cf.run(
            today=date(2026, 8, 15),
            sessions_path=self.sessions,
            log_path=self.dir / "eating-window.jsonl",
            raw_copy_path=self.dir / "fasts-copy.jsonl",
            state_path=state,
            quiet=True,
        )

        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(state.read_text()), prior)

    def test_a_degraded_run_writes_nothing_to_the_vault_log(self):
        log = self.dir / "eating-window.jsonl"
        (self.dir / ".fasts.jsonl.icloud").write_bytes(b"\x00placeholder")

        cf.run(
            today=date(2026, 8, 15),
            sessions_path=self.sessions,
            log_path=log,
            raw_copy_path=self.dir / "fasts-copy.jsonl",
            state_path=self.dir / "fasting.json",
            quiet=True,
        )

        self.assertFalse(log.exists())


class SyncingSessionsIntoTheLog(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.sessions = self.dir / "fasts.jsonl"
        self.log = self.dir / "eating-window.jsonl"

    def sync(self) -> list[dict]:
        records, status = cf.read_sessions(self.sessions)
        self.assertEqual(status, cf.OK)
        return cf.sync_days(cf.derive_days(records), self.log)

    def test_derived_days_land_in_the_log_with_source_app(self):
        write_lines(self.sessions, [session("2026-08-13T22:00:00-05:00", "2026-08-14T16:00:00-05:00")])

        written = self.sync()

        self.assertEqual(len(written), 1)
        self.assertEqual(written[0]["source"], "app")
        self.assertEqual(written[0]["date"], "2026-08-14")
        self.assertTrue(written[0]["held"])

    def test_running_twice_appends_nothing_the_second_time(self):
        write_lines(self.sessions, [session("2026-08-13T22:00:00-05:00", "2026-08-14T16:00:00-05:00")])
        self.sync()

        self.assertEqual(self.sync(), [])
        self.assertEqual(len(self.log.read_text().strip().splitlines()), 1)

    def test_an_edited_fast_appends_a_correction(self):
        write_lines(
            self.sessions,
            [session("2026-08-13T22:00:00-05:00", "2026-08-14T14:00:00-05:00", sid="x")],
        )
        self.sync()

        # He notices he forgot to tap end and fixes it in the app.
        write_lines(
            self.sessions,
            [
                session("2026-08-13T22:00:00-05:00", "2026-08-14T14:00:00-05:00", sid="x"),
                session("2026-08-13T22:00:00-05:00", "2026-08-14T16:30:00-05:00", sid="x"),
            ],
        )
        written = self.sync()

        self.assertEqual(len(written), 1)
        self.assertTrue(written[0]["held"])
        self.assertTrue(cf.load_log(self.log)[date(2026, 8, 14)]["held"])

    def test_it_does_not_fight_a_manual_override(self):
        # Alex said yes by hand. A later app record must not start a write war.
        write_lines(
            self.log,
            [{"date": "2026-08-14", "held": True, "source": "manual", "logged_at": "x"}],
        )
        write_lines(
            self.sessions,
            [session("2026-08-13T22:00:00-05:00", "2026-08-14T14:00:00-05:00")],
        )

        self.sync()

        self.assertTrue(cf.load_log(self.log)[date(2026, 8, 14)]["held"])


class TheWindow(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.log = self.dir / "eating-window.jsonl"

    def metric(self, today: date) -> dict:
        return cf.collect(today=today, log=cf.load_log(self.log))["metrics"]["fasting"]

    def test_a_weekend_is_never_a_miss(self):
        # Saturday 2026-08-15 is a Saturday; Sunday the 16th follows.
        m = self.metric(date(2026, 8, 16))

        # The window is Mon 10 .. Sun 16. Sat and Sun are the last two cells.
        self.assertIsNone(m["days"][-1])
        self.assertIsNone(m["days"][-2])
        self.assertEqual(m["unit_denominator"], 5)

    def test_an_unanswered_weekday_is_unknown_not_broken(self):
        write_lines(
            self.log, [{"date": "2026-08-10", "held": True, "source": "app", "logged_at": "x"}]
        )

        m = self.metric(date(2026, 8, 14))

        self.assertEqual(m["value"], 1)
        self.assertIn("unanswered", m["note"])
        self.assertNotIn(False, m["days"])

    def test_with_nothing_logged_the_value_is_none_not_zero(self):
        m = self.metric(date(2026, 8, 14))

        self.assertIsNone(m["value"])

    def test_a_full_week_scores_five_of_five(self):
        write_lines(
            self.log,
            [
                {"date": f"2026-08-{d:02d}", "held": True, "source": "app", "logged_at": "x"}
                for d in (10, 11, 12, 13, 14)
            ],
        )

        m = self.metric(date(2026, 8, 14))

        self.assertEqual(m["value"], 5)
        self.assertEqual(m["unit_denominator"], 5)
        self.assertEqual(m["note"], "")


class EndToEnd(unittest.TestCase):
    def test_a_real_session_file_becomes_a_habits_row_value(self):
        d = Path(tempfile.mkdtemp())
        sessions = d / "fasts.jsonl"
        write_lines(
            sessions,
            [
                session(f"2026-08-{day - 1:02d}T22:00:00-05:00", f"2026-08-{day:02d}T16:30:00-05:00", sid=str(day))
                for day in (11, 12, 13, 14)
            ],
        )
        state = d / "fasting.json"

        rc = cf.run(
            today=date(2026, 8, 14),
            sessions_path=sessions,
            log_path=d / "eating-window.jsonl",
            raw_copy_path=d / "fasts-copy.jsonl",
            state_path=state,
            quiet=True,
        )

        self.assertEqual(rc, 0)
        blob = json.loads(state.read_text())
        self.assertEqual(blob["metrics"]["fasting"]["value"], 4)
        self.assertTrue((d / "fasts-copy.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
