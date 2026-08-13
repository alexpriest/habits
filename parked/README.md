# Parked

Not wired into `habits` — no row, not in `SOURCES`, not run by `habits refresh`.

**Fasting / eating window** (`collect_fasting.py`, `import_zero.py`) — built
2026-08-13, parked the same day at Alex's word: *"let's just keep fasting off of
it, nbd"* and *"forget zero and fasting for now."*

Both work. `collect_fasting.py` scores the 18:6 against the 5 weekdays (weekends
are off by design and are NOT misses); `import_zero.py` ingests a Zero
"Download My Data" export and refuses to guess at a schema it does not
recognise. The blocker was never the code — Zero has no API, no HealthKit
fasting type exists for any app to write into, and macOS has no Health app, so
the export is the only path and it needs a tap on the phone every time.

To revive: add `"fasting"` back to `SOURCES` and to `DEFAULTS["metrics"]` in
`habits` (unit `weekdays`, threshold 4), and move these two files back up.
