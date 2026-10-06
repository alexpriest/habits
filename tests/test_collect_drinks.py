#!/usr/bin/env python3
"""Tests for the drinks collector. Run: python3.11 -m pytest tests -q"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import collect_drinks as cd  # noqa: E402

GOLDEN = (
    '{"at":"2026-10-06T21:14:00-05:00","id":"6F9619FF-8B86-D011-B42D-00C04FC964FF",'
    '"kind":"vermouth","source":"app"}'
)


def drink(at: str, sid: str, kind: str | None = None) -> dict:
    rec = {"at": at, "id": sid, "source": "app"}
    if kind:
        rec["kind"] = kind
    return rec


def deletion(sid: str) -> dict:
    return {"at": None, "deleted": True, "deleted_at": "2026-10-07T08:00:00-05:00", "id": sid}


def write(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records))


def counts_of(records: list[dict]) -> dict[date, int]:
    return cd.evening_counts(cd.resolve(records))


class Parsing(unittest.TestCase):
    def test_golden_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "drinks.jsonl"
            path.write_text(GOLDEN + "\n" + '{"at":"2026-10-07T2')  # half-written tail
            records, status = cd.read_drinks(path)
        self.assertEqual(status, cd.OK)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "vermouth")
        self.assertEqual(counts_of(records), {date(2026, 10, 6): 1})


class Attribution(unittest.TestCase):
    def test_one_am_belongs_to_previous_evening(self):
        c = counts_of([drink("2026-10-07T01:00:00-05:00", "a")])
        self.assertEqual(c, {date(2026, 10, 6): 1})

    def test_four_am_belongs_to_its_own_date(self):
        c = counts_of([drink("2026-10-07T04:00:00-05:00", "a")])
        self.assertEqual(c, {date(2026, 10, 7): 1})

    def test_uses_the_timestamps_own_offset(self):
        c = counts_of([drink("2026-10-07T21:30:00-05:00", "a")])
        self.assertEqual(c, {date(2026, 10, 7): 1})


class Supersede(unittest.TestCase):
    def test_last_record_per_id_wins(self):
        c = counts_of([
            drink("2026-10-06T21:00:00-05:00", "a"),
            drink("2026-10-07T20:00:00-05:00", "a", "wine"),
        ])
        self.assertEqual(c, {date(2026, 10, 7): 1})

    def test_deletion_is_terminal(self):
        c = counts_of([
            drink("2026-10-06T21:00:00-05:00", "a"),
            deletion("a"),
            drink("2026-10-06T22:00:00-05:00", "a"),  # must not resurrect
            drink("2026-10-06T22:30:00-05:00", "b"),
        ])
        self.assertEqual(c, {date(2026, 10, 6): 1})

    def test_unknown_kind_is_still_a_drink(self):
        c = counts_of([drink("2026-10-06T21:00:00-05:00", "a", "mead")])
        self.assertEqual(c, {date(2026, 10, 6): 1})


class Cells(unittest.TestCase):
    def test_before_tracking_none_closed_empty_dry_today_none(self):
        today = date(2026, 10, 8)
        m = cd.collect(today, [])["metrics"]["drinks"]
        # window Oct 2..8: 2-5 unknown, 6 and 7 dry, 8 (today) open
        self.assertEqual(m["days"], [None, None, None, None, True, True, None])
        self.assertEqual(m["counts"], [None, None, None, None, 0, 0, None])
        self.assertEqual(m["value"], 2)

    def test_nothing_observed_is_not_dry(self):
        m = cd.collect(date(2026, 10, 8), [], observed=False)["metrics"]["drinks"]
        self.assertIsNone(m["value"])
        self.assertEqual(m["note"], "not answered yet")
        self.assertTrue(all(c is None for c in m["days"]))


class Week(unittest.TestCase):
    def test_value_total_weeknights_over(self):
        # Window Tue Oct 6 .. Mon Oct 12. Today = Mon Oct 12 (open).
        recs = [
            drink("2026-10-06T21:00:00-05:00", "t1"),  # Tue: 3 -> over
            drink("2026-10-06T21:30:00-05:00", "t2"),
            drink("2026-10-07T01:30:00-05:00", "t3"),  # files under Tue
            drink("2026-10-07T20:00:00-05:00", "w1"),  # Wed: 2 -> ok
            drink("2026-10-07T21:00:00-05:00", "w2"),
            drink("2026-10-10T19:00:00-05:00", "s1"),  # Sat: 4, weekend
            drink("2026-10-10T20:00:00-05:00", "s2"),
            drink("2026-10-10T21:00:00-05:00", "s3"),
            drink("2026-10-10T22:00:00-05:00", "s4"),
            drink("2026-10-12T19:00:00-05:00", "m1"),  # today, open
        ]
        m = cd.collect(date(2026, 10, 12), recs)["metrics"]["drinks"]
        self.assertEqual(m["counts"], [3, 2, 0, 0, 4, 0, None])
        self.assertEqual(m["days"], [False, False, True, True, False, True, None])
        self.assertEqual(m["value"], 3)
        self.assertEqual(m["total"], 10)
        self.assertEqual(m["weeknights_over"], 1)
        self.assertEqual(m["note"], "10 drinks")


class Degraded(unittest.TestCase):
    def test_placeholder_keeps_state_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "drinks.jsonl"
            (tmp / ".drinks.jsonl.icloud").write_text("")
            state = tmp / "drinks-state.json"
            state.write_text('{"keep": "me"}')
            before = state.read_bytes()
            rc = cd.run(today=date(2026, 10, 8), drinks_path=src,
                        raw_copy_path=tmp / "copy.jsonl", state_path=state, quiet=True)
            self.assertNotEqual(rc, 0)
            self.assertEqual(state.read_bytes(), before)
            self.assertFalse((tmp / "copy.jsonl").exists())

    def test_absent_writes_state_with_no_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            state = tmp / "s.json"
            rc = cd.run(today=date(2026, 10, 8), drinks_path=tmp / "drinks.jsonl",
                        raw_copy_path=tmp / "copy.jsonl", state_path=state, quiet=True)
            self.assertEqual(rc, 0)
            m = json.loads(state.read_text())["metrics"]["drinks"]
            self.assertIsNone(m["value"])
            self.assertEqual(m["note"], "not answered yet")

    def test_ok_copies_raw_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "drinks.jsonl"
            write(src, [drink("2026-10-06T21:00:00-05:00", "a")])
            rc = cd.run(today=date(2026, 10, 8), drinks_path=src,
                        raw_copy_path=tmp / "out" / "copy.jsonl", state_path=tmp / "s.json", quiet=True)
            self.assertEqual(rc, 0)
            self.assertEqual((tmp / "out" / "copy.jsonl").read_bytes(), src.read_bytes())


if __name__ == "__main__":
    unittest.main()
