"""Tests for collect_api — the vendor-API collectors.

These exist because of a bug that lived the tool's entire life without being
noticed: `oura_sleep` passed `end_date=today` to an API whose `end_date` is
EXCLUSIVE, so it never saw last night. It did not look like a bug. It looked
like a ring that hadn't synced — a permanently blank newest cell and a note
that always read "6/7 nights". The tell was that it never once read 7/7.

The lesson generalises past Oura: when a date-ranged API silently returns one
fewer record than you asked for, assert the REQUEST, not just the response.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from unittest import mock

import collect_api


def sleep_period(day: date, hours: float, kind: str = "long_sleep") -> dict:
    return {"day": str(day), "total_sleep_duration": int(hours * 3600), "type": kind}


class OuraDateWindow(unittest.TestCase):
    """The request itself, because the response looks reasonable either way."""

    TODAY = date(2026, 8, 17)

    def call(self, periods: list[dict]) -> tuple[dict, dict]:
        """The fake honours Oura's EXCLUSIVE end_date.

        ⚠️ This matters more than it looks. A fake that returns every period
        regardless of the range makes the behavioural tests below pass against the
        buggy code too — they go green while the real collector loses a night. The
        first version of this file did exactly that, and only the assertion on the
        request itself went red. A mock that ignores the parameter under test
        cannot test that parameter.
        """
        seen: dict = {}

        def fake_get_json(url, headers, params=None):
            seen.update(params or {})
            lo = date.fromisoformat(params["start_date"])
            hi = date.fromisoformat(params["end_date"])  # exclusive, as Oura does it
            return {"data": [p for p in periods if lo <= date.fromisoformat(p["day"]) < hi]}

        with mock.patch.object(collect_api, "secret", return_value="tok"), \
             mock.patch.object(collect_api, "get_json", side_effect=fake_get_json):
            return collect_api.oura_sleep(self.TODAY), seen

    def test_end_date_is_exclusive_so_it_asks_for_tomorrow(self):
        _, params = self.call([sleep_period(self.TODAY, 6.88)])
        self.assertEqual(
            params["end_date"], str(self.TODAY + timedelta(days=1)),
            "Oura's end_date is exclusive — asking for `today` silently drops last night",
        )

    def test_start_date_reaches_back_the_full_history(self):
        _, params = self.call([sleep_period(self.TODAY, 6.88)])
        self.assertEqual(
            params["start_date"],
            str(self.TODAY - timedelta(days=collect_api.HISTORY_DAYS)),
        )

    def test_last_night_lands_in_the_newest_history_slot(self):
        """The whole point: a record for today must not fall off the end."""
        got, _ = self.call([sleep_period(self.TODAY, 6.88)])
        self.assertIsNotNone(
            got["history"][-1],
            "a record dated today must occupy the newest cell, not render blank",
        )
        self.assertAlmostEqual(got["history"][-1], 6.88, places=2)

    def test_a_full_window_reports_no_missing_nights(self):
        """`note` is the tell. A permanent '6/7 nights' means a permanent off-by-one."""
        window = [self.TODAY - timedelta(days=i) for i in range(collect_api.WINDOW_DAYS)]
        got, _ = self.call([sleep_period(d, 7.0) for d in window])
        # The note also carries the range now, so assert the ABSENCE of a
        # missing-nights clause rather than an empty string.
        self.assertNotIn("nights", got["note"], f"expected a clean 7/7, got {got['note']!r}")

    def test_a_genuinely_missing_night_still_reports_it(self):
        """The fix must not paper over a real gap — that was the original symptom."""
        window = [self.TODAY - timedelta(days=i) for i in range(collect_api.WINDOW_DAYS)]
        got, _ = self.call([sleep_period(d, 7.0) for d in window[1:]])  # today absent
        self.assertIn("6/7 nights", got["note"])
        self.assertIsNone(got["history"][-1])


class OuraDayIsTheWakeDate(unittest.TestCase):
    """Oura keys a night by when it ENDED, which is why the rightmost cell is
    last night and never 'tonight'. Recorded as a test so nobody re-derives it."""

    TODAY = date(2026, 8, 17)

    def collect(self, periods):
        def fake_get_json(url, headers, params=None):
            lo = date.fromisoformat(params["start_date"])
            hi = date.fromisoformat(params["end_date"])  # exclusive
            return {"data": [p for p in periods if lo <= date.fromisoformat(p["day"]) < hi]}

        with mock.patch.object(collect_api, "secret", return_value="tok"), \
             mock.patch.object(collect_api, "get_json", side_effect=fake_get_json):
            return collect_api.oura_sleep(self.TODAY)

    def test_naps_add_to_the_days_total(self):
        got = self.collect([
            sleep_period(self.TODAY, 7.54),
            sleep_period(self.TODAY, 0.25, kind="late_nap"),
        ])
        self.assertAlmostEqual(got["history"][-1], 7.79, places=2)

    def test_deleted_and_rest_periods_are_ignored(self):
        got = self.collect([
            sleep_period(self.TODAY, 7.0),
            sleep_period(self.TODAY, 3.0, kind="deleted"),
            sleep_period(self.TODAY, 2.0, kind="rest"),
        ])
        self.assertAlmostEqual(got["history"][-1], 7.0, places=2)

    def test_no_periods_at_all_raises_rather_than_reporting_zero(self):
        with self.assertRaises(collect_api.SourceError):
            self.collect([])


if __name__ == "__main__":
    unittest.main()
