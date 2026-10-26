#!/bin/sh
# Stop the kiosk browser and its relaunch loop, leaving the normal desktop. It comes back at next login.
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/jarvis-kiosk.pid"
if [ -f "$PIDFILE" ]; then
    LOOP=$(cat "$PIDFILE")
    kill "$LOOP" 2>/dev/null       # stop relaunching first
    pkill -P "$LOOP" 2>/dev/null   # then close the browser it started
    rm -f "$PIDFILE"
    echo "kiosk stopped"
else
    echo "kiosk not running"
fi
