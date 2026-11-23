#!/bin/zsh
# Run Audio Story Studio in the background on this Mac (launchd user agents):
#   worker    — processes the job queue
#   telegram  — Telegram long polling (no public URL needed; control everything from your phone)
#   api       — Worker API on 127.0.0.1:8765 (only needed for n8n)
# Usage: scripts/services.sh install [--with-api] | uninstall | status | logs [worker|telegram|api]
set -euo pipefail

ROOT="${0:A:h:h}"
PY="$ROOT/.venv/bin/python"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$ROOT/storage/logs"
PREFIX="com.audiostorystudio"
PATH_VALUE="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

plist() {  # name, module args...
  local name=$1; shift
  local args=""
  for a in "$PY" -m "$@"; do args+="    <string>$a</string>\n"; done
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$PREFIX.$name</string>
  <key>ProgramArguments</key>
  <array>
$(printf "$args")
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$PATH_VALUE</string><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$LOGS/$name.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$name.log</string>
</dict>
</plist>
EOF
}

load() {  # name, module args...
  local name=$1; shift
  local file="$AGENTS/$PREFIX.$name.plist"
  plist "$name" "$@" > "$file"
  launchctl bootout "gui/$(id -u)/$PREFIX.$name" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$file"
  echo "started $PREFIX.$name"
}

case "${1:-}" in
  install)
    [[ -x "$PY" ]] || { echo "missing $PY — create the venv first (see README)"; exit 1; }
    "$PY" -m studio.cli migrate
    mkdir -p "$AGENTS" "$LOGS"
    load worker studio.worker
    load telegram studio.telegram_poll
    [[ "${2:-}" == "--with-api" ]] && load api uvicorn studio.api:app --host 127.0.0.1 --port 8765
    echo "Logs: $LOGS/{worker,telegram,api}.log"
    ;;
  uninstall)
    for n in worker telegram api; do
      launchctl bootout "gui/$(id -u)/$PREFIX.$n" 2>/dev/null && echo "stopped $PREFIX.$n" || true
      rm -f "$AGENTS/$PREFIX.$n.plist"
    done
    ;;
  status)
    launchctl list | grep "$PREFIX" || echo "no services running"
    ;;
  plist)  # print a generated plist without installing (for checking)
    shift; plist "$@"
    ;;
  logs)
    tail -n 50 -f "$LOGS/${2:-worker}.log"
    ;;
  *)
    echo "usage: $0 install [--with-api] | uninstall | status | logs [worker|telegram|api]"; exit 2
    ;;
esac
