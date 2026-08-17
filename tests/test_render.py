"""Tests for the dashboard renderer — one grammar, one window, aligned.

The chart column was rebuilt 2026-08-17 after Alex said of the sparklines: "the
visual is not particularly useful" and "without headers I don't really know what
I'm looking at." Three defects were behind that, and each has a test here:

  1. Two spans in one column — strips covered 7 days, sparklines 13, under a
     header that said "LAST 7 DAYS". Now everything is 7.
  2. No labels — nothing said which cell was which day.
  3. The writing row drew NOTHING, which reads as broken rather than as "no
     daily series."

The alignment test is the one that matters most. A day-label row that does not
sit exactly over its cells is worse than no labels at all: it silently misreads
every column. Five iterations of the `left` build passed checks and still looked
wrong on screen, which is why this asserts the column index rather than eyeballs.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

# `habits` has no .py extension, so it needs loading by path.
#
# ⚠️ It MUST go into sys.modules before exec_module. `@dataclass` resolves its
# own module out of sys.modules to check for KW_ONLY, and on 3.14 an unregistered
# module makes that lookup return None — the import dies with a bare
# "'NoneType' object has no attribute '__dict__'" that says nothing about the
# real cause.
_spec = importlib.util.spec_from_loader(
    "habits_cli",
    importlib.machinery.SourceFileLoader(
        "habits_cli", str(Path(__file__).resolve().parent.parent / "habits")
    ),
)
habits = importlib.util.module_from_spec(_spec)
sys.modules["habits_cli"] = habits
_spec.loader.exec_module(habits)


class DayLabels(unittest.TestCase):
    def test_seven_labels_one_per_cell(self):
        self.assertEqual(len(habits.day_labels(date(2026, 8, 17)).split(" ")), 7)

    def test_rightmost_label_is_today(self):
        for day in (date(2026, 8, 17), date(2026, 8, 22), date(2026, 3, 1)):
            with self.subTest(day=day):
                self.assertEqual(
                    habits.day_labels(day).split(" ")[-1], day.strftime("%a")[0]
                )

    def test_labels_rotate_with_the_rolling_window(self):
        """The window rolls, so a hardcoded M T W T F S S would be wrong 6 days
        out of 7. Monday and Tuesday must not produce the same row."""
        mon = habits.day_labels(date(2026, 8, 17))  # a Monday
        tue = habits.day_labels(date(2026, 8, 18))
        self.assertEqual(mon, "T W T F S S M")
        self.assertEqual(tue, "W T F S S M T")
        self.assertNotEqual(mon, tue)

    def test_label_row_width_matches_a_strip(self):
        """Same character width, or the header cannot sit over the cells."""
        self.assertEqual(
            len(habits.day_labels(date(2026, 8, 17))),
            len(habits.strip([True] * 7)),
        )


class ThresholdCells(unittest.TestCase):
    """Sleep: continuous hours -> 7 hit/miss cells against the bar."""

    def test_scores_each_day_against_the_threshold(self):
        got = habits.cells_from_threshold([7.0, 5.5, 6.0, 6.9, 4.0, 8.1, 6.88], 6.0, "min")
        self.assertEqual(got, [True, False, True, True, False, True, True])

    def test_takes_only_the_last_seven_of_a_longer_series(self):
        """The collector keeps 14 nights; the dashboard shows 7. It must take the
        NEWEST 7 — an earlier renderer silently dropped the oldest of 14 to
        fit 13 cells, which is the same class of bug."""
        series = [1.0] * 7 + [9.0] * 7
        self.assertEqual(habits.cells_from_threshold(series, 6.0, "min"), [True] * 7)

    def test_none_stays_unknown_and_never_becomes_a_miss(self):
        got = habits.cells_from_threshold([7.0, None, 6.5, 7.1, 7.2, 7.3, 7.4], 6.0, "min")
        self.assertIsNone(got[1])
        self.assertNotIn(False, got, "a missing night must not read as a failed one")

    def test_all_none_is_no_data_not_seven_misses(self):
        self.assertIsNone(habits.cells_from_threshold([None] * 7, 6.0, "min"))

    def test_empty_and_absent_series_return_none(self):
        self.assertIsNone(habits.cells_from_threshold([], 6.0, "min"))
        self.assertIsNone(habits.cells_from_threshold(None, 6.0, "min"))

    def test_max_direction_inverts_the_comparison(self):
        got = habits.cells_from_threshold([10.0, 40.0], 30.0, "max")
        self.assertEqual(got, [True, False])


class Strip(unittest.TestCase):
    def test_unknown_hit_and_miss_are_three_distinct_glyphs(self):
        rendered = habits.strip([True, False, None])
        self.assertEqual(len({habits.STRIP_HIT, habits.STRIP_MISS, habits.STRIP_UNKNOWN}), 3)
        for g in (habits.STRIP_HIT, habits.STRIP_MISS, habits.STRIP_UNKNOWN):
            self.assertIn(g, rendered)

    def test_a_short_series_is_padded_so_columns_still_line_up(self):
        self.assertEqual(len(habits.strip([True, True])), len(habits.strip([True] * 7)))


class RenderedDashboard(unittest.TestCase):
    """End-to-end on the real render path, colour off."""

    def dashboard(self, state: dict) -> list[str]:
        return habits.render(habits.DEFAULTS, state, color=False).split("\n")

    def full_state(self) -> dict:
        week = [True, False, True, True, False, True, True]
        return {
            "lifts": {"value": 2, "days": week},
            "sleep": {"value": 6.9, "history": [7.0] * 13 + [5.0], "note": "5.0–7.0h"},
            "rides": {"value": 3, "days": week},
            "journal": {"value": 7, "days": [True] * 7},
            "writing": {"value": 0, "days": [False] * 6 + [True], "note": "last: x"},
            "outreach": {"value": 10, "days": week},
            "fasting": {"value": None, "days": [None] * 7},
        }

    def chart_rows(self, lines: list[str]) -> list[str]:
        """Metric rows only, identified by the status mark and the metric label.

        ⚠️ NOT by "contains a strip glyph". STRIP_UNKNOWN is `·`, which is also the
        separator in the `left` countdown footer ("48y 176d left · 2,529
        Saturdays"), so a glyph test swallowed that line — 8 rows for 7 metrics,
        and an alignment assertion that failed against the footer's bullet.
        """
        names = set(habits.DEFAULTS["metrics"])
        out = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] in ("●", "○", "▲", "—") and parts[1] in names:
                out.append(line)
        return out

    def test_every_metric_row_draws_a_chart(self):
        """The writing row rendered EMPTY before this. A blank column is
        indistinguishable from a broken one — that was Alex's question 3."""
        rows = self.chart_rows(self.dashboard(self.full_state()))
        self.assertEqual(len(rows), len(habits.DEFAULTS["metrics"]))
        glyphs = (habits.STRIP_HIT, habits.STRIP_MISS, habits.STRIP_UNKNOWN)
        for name in habits.DEFAULTS["metrics"]:
            row = next(r for r in rows if name in r)
            # ⚠️ Assert the CELLS, not the row. Counting rows passes even when a
            # row's chart column is blank — which is exactly the writing-row bug
            # this test is named for, and it slipped through the first version.
            cells = sum(row.count(g) for g in glyphs)
            self.assertEqual(
                cells, habits.STRIP_WIDTH,
                f"{name} drew {cells} cells, expected {habits.STRIP_WIDTH}: {row.strip()[:40]!r}",
            )

    def test_day_label_row_aligns_with_every_chart_row(self):
        lines = self.dashboard(self.full_state())
        labels = [l for l in lines if l.strip() and l.strip()[0].isalpha()
                  and len(l.strip()) == 13 and " " in l.strip()]
        label_row = next(l for l in labels if l.strip().replace(" ", "").isalpha())
        indent = len(label_row) - len(label_row.lstrip())
        glyphs = (habits.STRIP_HIT, habits.STRIP_MISS, habits.STRIP_UNKNOWN)
        for row in self.chart_rows(lines):
            first = min(row.index(g) for g in glyphs if g in row)
            self.assertEqual(
                first, indent,
                f"chart starts at {first} but labels at {indent}: {row.strip()[:30]!r}",
            )

    def test_no_row_shows_more_than_seven_cells(self):
        """The old sparkline drew 13 under a header that said 7."""
        lines = self.dashboard(self.full_state())
        glyphs = (habits.STRIP_HIT, habits.STRIP_MISS, habits.STRIP_UNKNOWN)
        for row in self.chart_rows(lines):
            self.assertLessEqual(
                sum(row.count(g) for g in glyphs), habits.STRIP_WIDTH,
                f"more than {habits.STRIP_WIDTH} cells in {row.strip()[:30]!r}",
            )

    def test_header_span_matches_the_number_of_cells(self):
        """The header's date range and the strip width must describe the same
        window. They disagreed for the entire life of the sparkline."""
        lines = self.dashboard(self.full_state())
        header = next(l for l in lines if "LAST 7 DAYS" in l)
        first = date.today() - timedelta(days=habits.STRIP_WIDTH - 1)
        self.assertIn(f"{first:%b %-d}", header)
        self.assertIn(f"{date.today():%b %-d}", header)

    def test_a_missing_source_renders_a_dash_not_a_zero(self):
        lines = self.dashboard(self.full_state())
        fasting = next(l for l in lines if "fasting" in l)
        self.assertIn("—", fasting)
        self.assertNotIn("0", fasting.split("≥")[0])

    def test_sparkline_helper_is_gone(self):
        """Deleted, not left behind as dead code. `history` is still collected and
        still drives `surging` — it just is not drawn."""
        self.assertFalse(hasattr(habits, "sparkline"))
        self.assertFalse(hasattr(habits, "SPARK"))


if __name__ == "__main__":
    unittest.main()
