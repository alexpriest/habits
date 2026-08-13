#!/bin/bash
# <xbar.title>habits</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>Kit</xbar.author>
# <xbar.desc>Rolling 7-day habit dashboard. Reads the same state file as the CLI.</xbar.desc>
#
# Menu bar reader for `habits`. It renders the SAME state file the CLI does —
# there is no second implementation and no API call here, so it is instant and
# can never disagree with the terminal.
#
# The menu bar shows one dot per metric so a glance is enough; the dropdown has
# the full table. Unlike the terminal, the menu bar renders in the system font,
# so the JetBrains Mono glyph constraints do not apply to the title line.

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

HABITS="$HOME/.local/bin/habits"
[ -x "$HABITS" ] || { echo "habits ✗"; echo "---"; echo "habits CLI not installed"; exit 0; }

STATE="$("$HABITS" --json 2>/dev/null)"
[ -n "$STATE" ] || { echo "habits ✗"; echo "---"; echo "no state"; exit 0; }

# One dot per metric, in the CLI's order: ● on track, ○ off, · no data.
python3 - "$STATE" <<'PY'
import json, sys

blob = json.loads(sys.argv[1])
cfg, state = blob["config"], blob["state"]

dots, off = [], []
for key, spec in cfg["metrics"].items():
    entry = state.get(key) or {}
    value = entry.get("value")
    if value is None:
        dots.append("·")
        continue
    threshold = spec["threshold"]
    bad = value > threshold if spec["direction"] == "max" else value < threshold
    dots.append("○" if bad else "●")
    if bad:
        off.append(spec.get("label", key))

# Title: the dots, plus a count only when something is actually wrong. A badge
# that is always lit stops carrying information.
title = "".join(dots)
if off:
    title += f"  {len(off)}"
print(title)
print("---")
if off:
    print(f"Off track: {', '.join(off)} | color=#d1495b")
    print("---")
PY

"$HABITS" --no-color | sed 's/^/ /' | while IFS= read -r line; do
    echo "$line | font=Menlo size=12 trim=false"
done

echo "---"
echo "Refresh now | bash=$HOME/.local/bin/habits param1=refresh terminal=false refresh=true"
echo "Open in terminal | bash=/usr/bin/open param1=-a param2=Ghostty terminal=false"
