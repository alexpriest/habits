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
| outreach | Gmail sent + iMessage | < 3 *initiated* this week |
| fasting | Window sessions via iCloud | < 4 of the 5 weekdays held |

Reading is deliberately *not* tracked — it's a question the Sunday text asks, and
the answer gets logged. Spending was cut.

**Outreach counts conversations STARTED, not messages sent.** "Messages sent" is
265 a week, mostly to Miranda and to live group chats, and would read as a hit
forever. A conversation counts only when Alex sent the first message after 14
days of silence — which surfaces the reconnections (Adam Pelavin after 49 days)
and ignores the daily traffic. On email the test is different and simpler: the
thread's *first sender* must be Alex, because `in:sent` labels an entire thread
if any one message in it was sent, so replying to a marketing blast otherwise
drags that blast in under its own subject.

**Fasting reads from Window** (`~/Code/projects/window`), Alex's own iOS tracker.
It was parked 2026-08-13 because capture was the problem — Zero has no API,
HealthKit has no fasting type, and macOS has no Health app — and un-parked
2026-08-15 when he approved building the app. Window writes one line per
completed fast to its iCloud container; `collect_fasting.py` turns those into
one verdict per day. **The app records facts and this repo makes the judgment**,
so the 18h threshold and the Mon–Fri denominator stay in a file he can edit
rather than in a TestFlight build.

## Layout

```
habits               the CLI (renders only)
collect_local.py     vault-backed: journal, writing
collect_api.py       Hevy, Oura, Strava
collect_outreach.py  iMessage + Gmail, conversations started
mcp_client.py        minimal MCP Streamable-HTTP client (rides)
collect_fasting.py   Window sessions (iCloud) -> one verdict per day
import_zero.py       one-off backfill from a Zero "Download My Data" export
tests/               unittest; python3 -m unittest discover -s tests -t .
sunday_text.py       the weekly accountability text
install.sh           symlink, config, launchd — per machine
```

- Config: `~/.config/habits/config.json` (thresholds; falls back to `DEFAULTS`)
- State: `~/Code/tools/habits/state/{local,api,outreach,fasting}.json`
- Logs: `~/.local/state/habits/com.alexpriest.habits-*.log` (launchd stdout/stderr)

State lives **in the repo**, not under `~/.local/state`, because `~/Code` is what
Syncthing carries between the two Macs — so the MacBook renders the same numbers
without running a collector. That is also why only the Mini owns the refresh
timer: two machines collecting into one synced file would be two writers racing.

**One state file per writer.** `collect_local.py` → `local.json`, `collect_api.py`
→ `api.json`, `collect_outreach.py` → `outreach.json`, `collect_fasting.py` →
`fasting.json`. Two writers on one file would clobber — the same failure that
silently ate 53 of 60 concurrent vault writes.

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
- ✅ Outreach collector (iMessage + Gmail), conversations started
- ✅ Fasting row, eating-window log, Zero export importer
- ✅ SwiftBar menu bar reader
- ✅ `habits refresh` / `habits journal` / `habits fast`
- ✅ install.sh + launchd refresh (Mini, every 2h + at load)
- ✅ Hevy and Strava self-refresh from 1Password (live since 2026-08-13)
- ✅ **Oura sleep live 2026-08-17** — Alex minted a PAT; 200 on
      `/v2/usercollection/sleep`, 6h58 over 6/7 nights. ANT-470 closed. See
      *Credentials* for the duplicate-title trap it left behind.
- 🔴 **The weekly text has never delivered.** The plist IS loaded (`--with-text`
      was run 2026-08-13), and its first scheduled run — Sun 2026-08-16 21:00 —
      died with `subprocess.TimeoutExpired` after 60s on `imsg send`. Nothing
      reached Alex's thread (verified against `chat.db`; last message there is
      2026-08-14). Tracked in ANT-486.
- ⬜ Fasting shows `—` until Window is on his phone (needs TestFlight, ANT-472)

## Credentials

`collect_api.py` reads env first, then 1Password. Nothing is passed on a command
line, so no secret lands in shell history or a process list.

| Item | State | Where to get it |
|---|---|---|
| `op://Claude/Hevy API/credential` | ✅ live | hevy.com/settings?developer |
| `op://Claude/Strava MCP/*` | ✅ live | the refresh token stays on the Railway volume; nothing here expires |
| `op://Claude/<item-id>/pat` | ✅ live | cloud.ouraring.com/personal-access-tokens — **a PAT, not the MCP OAuth passcode** |

🚨 **TWO items in the Claude vault are both titled `Oura MCP`, so every by-title
`op read` against that name fails.** Not "picks the wrong one" — `op` refuses:
`could not get item Claude/Oura MCP: More than one item matches "Oura MCP"`. The
older item (`522anp…`, 2026-08-13) holds the dead `credential` / MCP OAuth
passcode; the newer one (`kjib67…`, 2026-08-17) holds the working `pat`. That is
why `oura_sleep()` carries a **by-item-ID** ref in the middle of its list — it is
the only one that resolves today.

The list is ordered so it survives a cleanup: if the two items are ever merged
into one, the by-title `pat` ref starts winning and the ID ref becomes dead
weight. Nothing in the code needs to change when that happens.

📌 There is no `Oura API` item and there never was — that reference (fixed in
17a864c) was the *first* of the three faults here. The second was that
`credential` holds an OAuth passcode, not a PAT. The third was this title
collision. Each one presented as "sleep is stale."

Rides do not go through a token at all — they go through the deployed
`strava-mcp-server`, which speaks the **older HTTP+SSE** MCP transport (`/sse` +
`/message`; `/mcp` 404s). `mcp_client.py` exists for exactly that.
