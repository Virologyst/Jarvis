#!/bin/sh
# Pick up new code after a `git pull`: reinstall if dependencies changed, restart the
# terminal service, then reload the kiosk browser.
REPO="$HOME/Jarvis"
"$REPO/.venv/bin/pip" install -q --disable-pip-version-check -e "$REPO/protocol" -e "$REPO/terminal"
systemctl --user restart jarvis-terminal.service
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/jarvis-kiosk.pid"
if [ -f "$PIDFILE" ]; then
    pkill -P "$(cat "$PIDFILE")" 2>/dev/null   # the kiosk loop relaunches the browser on fresh code
fi
echo "restarted"
