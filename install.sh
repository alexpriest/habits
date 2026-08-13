#!/bin/bash
# Per-machine install for `habits`. Safe to re-run.
#
#   ./install.sh                # symlink, config, and the refresh timer
#   ./install.sh --with-text    # ALSO schedule the weekly accountability text
#
# ~/.local/bin does not sync between the two Macs, so this runs on each one.
# The state files DO sync (they live in ~/Code, which Syncthing carries), which
# is why only the always-on Mini runs the refresh timer: two machines refreshing
# the same state would be two writers racing on one file.
#
# The weekly text is opt-in on purpose. Everything else here is inert — it reads
# data and draws a table. That job SENDS A MESSAGE, so scheduling it is a
# separate, deliberate act rather than a side effect of installing a CLI.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/habits"
AGENTS="$HOME/Library/LaunchAgents"
MINI_HOST="Alexs-Mac-Mini.local"

WITH_TEXT=0
[[ "${1:-}" == "--with-text" ]] && WITH_TEXT=1

mkdir -p "$BIN" "$CONFIG_DIR" "$AGENTS" "$REPO/state"

# ---------------------------------------------------------------- the CLI
ln -sfn "$REPO/habits" "$BIN/habits"
chmod +x "$REPO/habits" "$REPO"/*.py
echo "  linked $BIN/habits -> $REPO/habits"

# ------------------------------------------------------------- thresholds
# Written only if absent: this is Alex's file once it exists, and an install
# must never quietly reset a threshold he tuned.
if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
  cat > "$CONFIG_DIR/config.json" <<'JSON'
{
  "metrics": {
    "lifts":    { "threshold": 1 },
    "sleep":    { "threshold": 6.0 },
    "rides":    { "threshold": 3 },
    "fasting":  { "threshold": 4 },
    "journal":  { "threshold": 4 },
    "writing":  { "threshold": 30 },
    "outreach": { "threshold": 3 }
  },
  "surge_multiplier": 1.5,
  "stale_after_hours": 26
}
JSON
  echo "  wrote $CONFIG_DIR/config.json"
else
  echo "  kept   $CONFIG_DIR/config.json (already yours)"
fi

# --------------------------------------------------------------- plists
write_plist() {  # label, interval-or-calendar-xml, program-args-xml
  local label="$1" schedule="$2" args="$3"
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
$args
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>GOG_ACCOUNT</key><string>hello@alexpriest.com</string>
  </dict>
$schedule
  <key>StandardOutPath</key><string>$HOME/.local/state/habits/$label.log</string>
  <key>StandardErrorPath</key><string>$HOME/.local/state/habits/$label.log</string>
</dict>
</plist>
PLIST
}

mkdir -p "$HOME/.local/state/habits"

reload() {  # label — bootout is expected to fail on first install
  local label="$1"
  launchctl bootout "gui/$UID/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$AGENTS/$label.plist"
  echo "  loaded $label"
}

# ------------------------------------------------------- refresh (Mini only)
REFRESH=com.alexpriest.habits-refresh
write_plist "$REFRESH" \
  "  <key>StartInterval</key><integer>7200</integer>
  <key>RunAtLoad</key><true/>" \
  "    <string>$BIN/habits</string>
    <string>refresh</string>"

if [[ "$(hostname)" == "$MINI_HOST" ]]; then
  reload "$REFRESH"
else
  echo "  wrote  $REFRESH.plist (NOT loaded — the Mini owns the refresh)"
fi

# ------------------------------------------------------- weekly text (opt-in)
TEXT=com.alexpriest.habits-weekly-text
write_plist "$TEXT" \
  "  <key>StartCalendarInterval</key>
  <dict>
    <key>Weekday</key><integer>0</integer>
    <key>Hour</key><integer>21</integer>
    <key>Minute</key><integer>0</integer>
  </dict>" \
  "    <string>/usr/bin/env</string>
    <string>python3</string>
    <string>$REPO/sunday_text.py</string>"

if [[ "$WITH_TEXT" == "1" && "$(hostname)" == "$MINI_HOST" ]]; then
  reload "$TEXT"
elif [[ "$WITH_TEXT" == "1" ]]; then
  echo "  wrote  $TEXT.plist (NOT loaded — the Mini owns scheduled sends)"
else
  echo "  wrote  $TEXT.plist (NOT loaded — re-run with --with-text to schedule it)"
fi

echo
echo "  habits            the dashboard"
echo "  habits refresh    run every collector"
echo "  habits journal    day-by-day behind the journal row"
echo "  habits fast --week mon,tue,fri    log the eating window"
echo
