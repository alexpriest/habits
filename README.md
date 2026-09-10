# habits

A dashboard for the habits a machine can already see, so nothing has to be ticked off by hand.

## Status

Shipped — runs daily on a Mac Mini and sends a Sunday-night accountability text.

## License

Not licensed for reuse.

```
  LAST 7 DAYS  Aug 11–Aug 17      ● on track   ○ off   ▲ surging   — no data
                              T W T F S S M

  ● lifts      2       ≥1     ■ □ □ ■ □ □ □
  ● sleep      6h58    ≥6h    ■ ■ ■ □ ■ ■ ■   5.6–7.8h
  ● rides      3       ≥3     □ ■ ■ □ ■ □ □   ≥30min, no e-bike
  ● journal    7/7     ≥4     ■ ■ ■ ■ ■ ■ ■
  ● writing    0d      ≤30d   □ □ □ □ □ □ ■   last: "Fuck it, let's go", Aug 17
  ● outreach   10      ≥3     ■ ■ ■ ■ □ □ □
  — fasting    —       ≥4     · · · · · · ·   not answered yet

  48y 176d left · 2,529 Saturdays · 43.6% spent
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
forever. A conversation counts only when Alex sent the first message after **30**
days of silence, which surfaces the reconnections (Adam Pelavin after 49) and
ignores the daily traffic. Email gets the same dormancy test plus one extra rule:
the thread's *first sender* must be Alex, because `in:sent` labels an entire
thread if any one message in it was sent, so replying to a marketing blast would
otherwise drag that blast in under its own subject.

⚠️ **Known weakness: the test is DORMANCY, not relationship.** A vendor emailed
every five weeks passes it forever. In the 2026-08-17 window, 5 of 10 hits were
transactional — a utility complaint, a support ticket, a car detailer, a "have a
great vacation", and a bike-shop delivery ETA. That last one is the *same* vendor
the 2026-08-13 dormancy fix was written to exclude, which is the tell that a time
threshold cannot express what this metric is actually for. The verdict does not
flip (5 real reconnections still clears ≥3), so it ships as-is and is written
down rather than quietly tuned. `gap_days` is also hardcoded `null` on the email
path, so email cannot show its work.

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
carries its age, and a day with no data is `·`, never a miss. It cost two rounds
to get this right: the first history pass drew five weeks of floor blocks for
weeks that simply predate the Craft mirror (which starts 2026-07-16), which reads
as *"you journaled nothing"* rather than *"we don't know."* Partial weeks are
unknown too — the week of Jul 13 has 4 of 7 days mirrored and scored 0.

**Rolling 7 days, never a calendar week.** A calendar week resets every Monday,
so the number is meaningless until Sunday, and it needed a whole pace/days-left
apparatus just to avoid reading as failure all week. A rolling window always
means the same thing on any day — and deleting the calendar week deleted that
code. Where a calendar week still matters (the Sunday text), it starts Monday.

**One grammar, one window, every row.** Every chart is a 7-day strip covering the
same days as the header, with a weekday row above it. `■` did it, `□` didn't, `·`
unknown. The strip shows *which* day was missed, not just how many.

Continuous series used to get a sparkline instead. That is gone (2026-08-17), and
the reason is worth keeping: it drew **13** cells under a header reading LAST 7
DAYS, unlabelled, scaled from Alex's worst night rather than from zero — so the
shape could not be decoded without a range nobody printed. His verdict: *"the
visual is not particularly useful"* and *"without headers I don't really know what
I'm looking at."* Magnitude a binary cell drops now goes in the note as text
(sleep prints `5.6–7.8h`), which needs no legend. `history` is still collected and
still drives `surging`; it just is not drawn.

**Look at the glyphs on the real terminal — and stack the rows.** This one has now
been learned three times, on two axes:

1. The `left` build passed four checks and still rendered wrong on screen.
2. Journaling shipped as an eighth-block sparkline anchored at zero. Values high
   and flat (6–7 of 7) filled every cell, adjacent cells merged **horizontally**,
   and it read as a redaction. It was then defended on theory — *"flat should look
   flat"* — in the message right after promising to look at the real screen first.
3. The strip shipped with `█`/`░`, which paint the full character cell including
   its leading, so filled cells fused **vertically** into one solid bar down the
   column. `--glyph-test` could not catch it: it printed ONE row per candidate and
   only ever checked horizontal alignment.

`habits --glyph-test` now stacks seven rows in a jagged pattern and says to look
*down* the columns. An ascending staircase, or a single row, hides exactly the
defect you are looking for. Braille and box-drawing corners failed and are not
used.

## Known limits

**Journaling was `provisional`, and now isn't — because the hole got closed at
the source.** The rule is Alex's: a Craft daily note with paragraph prose (not
just tasks) *or* a voice memo. Calendar blocks, tasks, bullets, agent callouts and
bold-lead paragraphs are excluded, and anything below the `---` divider is ignored
outright.

None of that could catch plain agent prose above the divider — a machine writes
good paragraphs, and no text heuristic separates them from Alex's. The 2026-08-06
note (an agent's wagyu cooking guide) scored as him journaling. So the row shipped
a `[provisional]` tag from 2026-08-11.

Fixed 2026-08-17 by ruling rather than by parsing: **no agent prose goes in a
daily note, ever — only tasks, or a link to a separate page.** It lives in the
global `CLAUDE.md`, so it binds every agent in every directory. A measurement of
Alex's own behaviour cannot be made trustworthy by better parsing of the machine's
output; it is made trustworthy by the machine not writing there. The tag came off.

⚠️ Two things this leaves open. Notes written **before 2026-08-17** predate the
rule and may still contain agent prose; the rolling 7-day window clears them by
**2026-08-24**. And the rule is enforced by instruction, not by code — which is
why `habits journal` still prints a reason per day. That view is the audit, and it
is the reason a violation would be visible rather than silent.

## Status

- ✅ CLI, renderer, rolling-7 windows, 7-day strips + weekday labels, config,
      state merge
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
| `op://Claude/Oura MCP/pat` | ✅ live | cloud.ouraring.com/personal-access-tokens — **a PAT, not the MCP OAuth passcode** |

⚠️ **`pat` and `credential` live in the same item and are not interchangeable.**
`credential` is an MCP OAuth passcode for the Railway MCP server; against
`api.ouraring.com` it 401s. Only `pat` works for this collector.

📌 **"Sleep is stale" was three different faults in a row, and the third is the
one to remember.** (1) The collector read a nonexistent `Oura API` item — fixed in
`17a864c`. (2) The item it then read holds an OAuth passcode, not a PAT — needed a
token Alex had to mint. (3) He minted it into a **second item with the identical
title**, and `op read` refuses an ambiguous title outright rather than picking one:

```
could not get item Claude/Oura MCP: More than one item matches "Oura MCP"
```

That broke *every* by-title ref against that name, including the `credential` one
that had resolved an hour earlier. The items were merged 2026-08-17, so a by-title
read works again. **If one ever fails here, check for a duplicate title before
suspecting the token** — the error looks nothing like the cause.

Rides do not go through a token at all — they go through the deployed
`strava-mcp-server`, which speaks the **older HTTP+SSE** MCP transport (`/sse` +
`/message`; `/mcp` 404s). `mcp_client.py` exists for exactly that.
