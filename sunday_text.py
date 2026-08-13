#!/usr/bin/env python3
"""The weekly accountability text — an exception report, not a status dump.

    python3 sunday_text.py --dry-run     # print it, send nothing
    python3 sunday_text.py               # send it

Only two things earn a line: a habit that is OFF TRACK, and one that is notably
MORE active than usual. Everything else collapses into a single reassurance line.
A weekly text that lists seven habits every week is a text you stop reading, and
the whole tool is worth nothing the week he stops reading it.

Then the questions. Reading is deliberately never scraped — it is asked, and the
answer is logged. Fasting is asked only when Zero's export has not covered the
week, so the question disappears on its own as soon as he imports.

⚠️ Plain text only. No markdown — asterisks and backticks render literally in
iMessage and look like a bug.

⚠️ This is the one part of `habits` that talks to Alex unprompted. It sends to
him and to nobody else: RECIPIENT is a constant, never an argument, so no future
edit can accidentally point a habit report at a third party.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
RECIPIENT = "+12702871307"  # Alex. Deliberately not configurable.
IMSG = "/opt/homebrew/bin/imsg"


def load_renderer():
    """Import the `habits` CLI so the text and the dashboard can never disagree.

    An explicit SourceFileLoader is required: `habits` has no .py suffix, and
    spec_from_file_location silently returns None for an unrecognised extension
    rather than raising, which surfaces as an unhelpful NoneType error later.
    """
    path = HERE / "habits"
    loader = importlib.machinery.SourceFileLoader("habits_cli", str(path))
    spec = importlib.util.spec_from_loader("habits_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    # Must be registered BEFORE exec: @dataclass resolves its own class's module
    # out of sys.modules, and an unregistered module makes that lookup return
    # None deep inside dataclasses.py with a misleading error.
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


def phrase(r) -> str:
    """One line, in the units a person thinks in."""
    if r.unit == "daysago":
        return f"{r.label} — {int(r.value)} days since you published (target: under {int(r.threshold)})"
    if r.unit == "hours":
        return f"{r.label} — averaging {r.value:.1f}h a night (target: {r.threshold:g}h)"
    if r.unit == "days7":
        return f"{r.label} — {int(r.value)} of 7 days (target: {int(r.threshold)})"
    if r.unit == "weekdays":
        return f"{r.label} — held {int(r.value)} of {r.denominator} weekdays (target: {int(r.threshold)})"
    return f"{r.label} — {int(r.value)} this week (target: {int(r.threshold)})"


def compose(mod) -> tuple[str, dict]:
    cfg = mod.load_config()
    state = mod.load_state()
    readings = mod.build_readings(cfg, state)
    mult = cfg.get("surge_multiplier", 1.5)

    # The week that just ended, Monday-started. This is the one place a calendar
    # week is the right frame — the rolling window is for the daily dashboard.
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    if today.weekday() == 6:  # Sunday: the week being closed out is this one
        monday = today - timedelta(days=6)

    off = [r for r in readings if not r.missing and r.off_track]
    surging = [r for r in readings if not r.missing and r.surging(mult)]
    fine = [r for r in readings if not r.missing and not r.off_track and r not in surging]
    unknown = [r for r in readings if r.missing]

    lines = [f"Week of {monday:%b %-d}."]

    if off:
        lines.append("")
        lines.append("Off track:")
        lines += [f"- {phrase(r)}" for r in off]
    if surging:
        lines.append("")
        lines.append("Notably up:")
        lines += [f"- {phrase(r)}" for r in surging]

    if fine:
        held = ", ".join(f"{r.label} {mod.fmt_value(r)}" for r in fine)
        lines.append("")
        lines.append(("Everything else held: " if off or surging else "All held: ") + held + ".")
    elif not off and not surging:
        lines.append("")
        lines.append("No data this week — the collectors are not reporting.")

    if unknown:
        lines.append("")
        lines.append("No data for: " + ", ".join(r.label for r in unknown) + ".")

    questions = ["Read anything worth remembering this week?"]
    fasting = next((r for r in readings if r.key == "fasting"), None)
    if fasting is not None and (fasting.missing or "unanswered" in (fasting.note or "")):
        questions.append("Eating window — which weekdays did you hold it? (e.g. mon,tue,fri / all / none)")

    lines.append("")
    if len(questions) == 1:
        lines.append(questions[0])
    else:
        lines += [f"{i}. {q}" for i, q in enumerate(questions, 1)]

    return "\n".join(lines), {"off": len(off), "surging": len(surging), "questions": len(questions)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Weekly habits accountability text.")
    ap.add_argument("--dry-run", action="store_true", help="print, do not send")
    args = ap.parse_args()

    mod = load_renderer()
    body, meta = compose(mod)

    if args.dry_run:
        print("-" * 58)
        print(body)
        print("-" * 58)
        print(f"to {RECIPIENT} | {meta['off']} off track, {meta['surging']} up, "
              f"{meta['questions']} question(s) | {len(body)} chars")
        return 0

    if not Path(IMSG).exists():
        print(f"habits: {IMSG} not found — not sent", file=sys.stderr)
        return 1

    proc = subprocess.run(
        [IMSG, "send", "--to", RECIPIENT, "--text", body],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        print(f"habits: send failed: {proc.stderr.strip()[:200]}", file=sys.stderr)
        return 1
    print(f"sent to {RECIPIENT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
