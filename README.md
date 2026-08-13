# habits

A CLI dashboard for the habits a machine can already see, plus a Sunday-night
accountability text. No manual tracking, ever — if a habit leaves digital
exhaust, ticking a box for it is input tax that produces no new information.

```
  WEEK OF AUG 10        ● hit   · on pace   ○ off   ▲ surging   — no data

  — lifts      —       ≥1
  — sleep      —       ≥6h
  — rides      —       ≥3
  · journal    2/7     ≥4          ▆██  5d left in week  [provisional]
  ○ writing    154d    ≤30d             last: "South by AI", Mar 10
  — outreach   —       ≥3

  48y 182d left · 2,530 Saturdays · 43.6% spent
```

## The plan

One engine, several surfaces. Everything slow — API calls, vault scans — runs in
the collectors on a schedule and lands in a state file. The CLI, the menu bar
reader, and the Sunday text all just *render* that file, so pulling the dashboard
up is instant. A dashboard that takes four seconds to hit six APIs is a dashboard
you stop opening.

| | Source | Off track when |
|---|---|---|
| lifts | Hevy | 0 this week |
| sleep | Oura | 7-day average < 6h |
| rides | Strava, ≥30 min | < 3 this week |
| journal | Craft daily notes (mirror) | < 4 days this week |
| writing | `Site/Writing` published (content vault) | nothing in > 30 days |
| outreach | Gmail sent + iMessage | < 3 initiated this week |

Reading is deliberately *not* tracked — it's a question the Sunday text asks, and
the answer gets logged. Spending was cut.

## Layout

```
habits              the CLI (renders only)
collect_local.py    vault-backed metrics: journal, writing
install.sh          symlink + config, per machine
```

- Config: `~/.config/habits/config.json` (thresholds; falls back to `DEFAULTS`)
- State: `~/.local/state/habits/{local,mcp}.json`

**One state file per writer.** Local collectors write `local.json`; the
MCP-backed metrics land in `mcp.json`. Two writers on one file would clobber —
the same failure that silently ate 53 of 60 concurrent vault writes.

## Rules this thing was built around

**Never show a number you don't have.** A missing source renders `—`, a stale one
carries its age, and a `None` in a sparkline is a blank cell. It cost two rounds
to get this right: the first history pass drew five weeks of floor blocks for
weeks that simply predate the Craft mirror (which starts 2026-07-16), which reads
as *"you journaled nothing"* rather than *"we don't know."* Partial weeks are
unknown too — the week of Jul 13 has 4 of 7 days mirrored and scored 0.

**Rolling 7 days, never a calendar week.** A calendar week resets every Monday,
so the number is meaningless until Sunday, and it needed a whole pace/days-left
apparatus just to avoid reading as failure all week. A rolling window always
means the same thing on any day — and deleting the calendar week deleted that
code. Where a calendar week still matters (the Sunday text), it starts Monday.

**Pick the mark by the shape of the data.** Daily yes/no habits get a 7-day
strip (`●●●●○●●`) — one cell per day of the window, which shows *which* day was
missed, not just how many. Continuous series (sleep hours) get a sparkline.

This was learned the ugly way. Journaling first shipped as an eighth-block
sparkline anchored at zero, which was mathematically honest and visually a
**solid black bar**: values sitting high and flat (6–7 of 7) fill every cell to
near-full height, adjacent cells merge, and the result reads as a redaction.
Worse, it was defended on theory — *"flat should look flat"* — in the message
right after promising to look at things on the real screen first.

**Look at the glyphs on the real terminal.** `▁▂▃▄▅▆▇█` was verified in Ghostty /
JetBrains Mono with a *jagged* test row — an ascending staircase hides
misalignment, which is how the `left` build passed four checks and still rendered
wrong on screen. `habits --glyph-test` reprints the candidates. Braille and
box-drawing corners failed and are not used.

## Known limits

**Journaling is `provisional`.** The rule is Alex's: a Craft daily note with
paragraph prose (not just tasks) *or* a voice memo. Calendar blocks, tasks,
bullets, agent callouts and bold-lead paragraphs are excluded — but plain
agent-written prose is not distinguishable from Alex's by any text heuristic, and
the 2026-08-06 note (an agent's wagyu cooking guide) still scores as journaled.
It survives because the metric is a weekly count against a threshold of 4, where
one false positive rarely flips the verdict, and because the day-by-day is
printed rather than buried.

## Status

- ✅ CLI, renderer, rolling-7 windows, 14-day sparklines, config, state merge
- ✅ Local collectors: journal (+ 8-week history), writing
- ⬜ Strava / Hevy / Oura collectors
- ⬜ SwiftBar menu bar reader
- ⬜ Refresh job (launchd, Mini) + Sunday 9pm text
- ⬜ `habits journal` day-by-day subcommand
