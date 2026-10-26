#!/bin/sh
# Full-screen Chromium on the Jarvis page. Started at desktop login by ~/.config/autostart/jarvis-kiosk.desktop.
# Waits for the terminal service to answer, then keeps the browser running (relaunches it if it exits).
# Stop with pi/kiosk-stop.sh.

URL="http://localhost:8080"
PIDFILE="${XDG_RUNTIME_DIR:-/tmp}/jarvis-kiosk.pid"
echo $$ > "$PIDFILE"

until curl -s -o /dev/null "$URL"; do sleep 1; done

while true; do
    chromium \
        --kiosk --app="$URL" \
        --user-data-dir="$HOME/.config/jarvis-kiosk" \
        --noerrdialogs --disable-infobars --no-first-run \
        --disable-session-crashed-bubble --disable-features=Translate \
        --password-store=basic --check-for-update-interval=31536000 \
        --ozone-platform=wayland
    sleep 2
done
