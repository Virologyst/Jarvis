#!/usr/bin/env bash
# One-shot Pi 5 setup for a Jarvis terminal. Safe to re-run. Needs no sudo
# (it tells you if a system package is missing).
#   ~/Jarvis/pi/install.sh
# Expects: the repo at ~/Jarvis, Raspberry Pi OS with desktop (labwc), desktop autologin,
# a USB speakerphone plugged in, and ~/Jarvis/.env with JARVIS_BRAIN_URL pointing at the brain.
set -euo pipefail

REPO="$HOME/Jarvis"
PI="$REPO/pi"

echo "== system packages"
if /sbin/ldconfig -p | grep -q libportaudio.so.2; then
    echo "ok   PortAudio"
else
    echo "MISSING PortAudio: run 'sudo apt install -y libportaudio2' then re-run this script"
    exit 1
fi

echo "== Python environment (terminal only: no GPU or Claude libraries)"
python3 -m venv "$REPO/.venv"
PIP="$REPO/.venv/bin/pip"
# openwakeword's packaging demands tflite-runtime on Linux, which has no wheel for the Pi's
# Python 3.13. Jarvis runs the wake word with ONNX instead (see terminal/jarvis_terminal/wake.py),
# so install openwakeword without that requirement and give it the ONNX runtime it actually uses.
"$PIP" install -q --disable-pip-version-check --no-deps "openwakeword>=0.6"
"$PIP" install -q --disable-pip-version-check onnxruntime requests scikit-learn scipy tqdm
# The terminal's other dependencies, read from its pyproject.toml (minus openwakeword, done above)
DEPS=$("$REPO/.venv/bin/python" -c "
import tomllib
deps = tomllib.load(open('$REPO/terminal/pyproject.toml', 'rb'))['project']['dependencies']
print(' '.join(repr(d.split('#')[0].strip()) for d in deps if not d.startswith('openwakeword')))")
eval "\"$PIP\" install -q --disable-pip-version-check $DEPS"
"$PIP" install -q --disable-pip-version-check --no-deps -e "$REPO/protocol" -e "$REPO/terminal"
echo "== wake word model"
"$REPO/.venv/bin/python" -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"

echo "== config"
if [ ! -f "$REPO/.env" ]; then
    cp "$REPO/.env.example" "$REPO/.env"
    echo "WARN created $REPO/.env from the example: set JARVIS_BRAIN_URL to ws://<brain-pc-ip>:8765/ws"
fi
grep -q "^JARVIS_BRAIN_URL=ws://localhost" "$REPO/.env" \
    && echo "WARN JARVIS_BRAIN_URL still points at localhost; the brain runs on the PC" \
    || echo "ok   brain URL set"

echo "== jarvis-terminal service"
mkdir -p "$HOME/.config/systemd/user"
cp "$PI/jarvis-terminal.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable jarvis-terminal.service
systemctl --user restart jarvis-terminal.service

echo "== kiosk autostart"
chmod +x "$PI"/*.sh
mkdir -p "$HOME/.config/autostart"
sed "s|@KIOSK@|$PI/kiosk.sh|" "$PI/jarvis-kiosk.desktop" > "$HOME/.config/autostart/jarvis-kiosk.desktop"

echo "== checks"
grep -rqs "^autologin-user=$USER" /etc/lightdm/ && echo "ok   desktop autologin" \
    || echo "WARN no desktop autologin: sudo raspi-config > System > Boot / Auto Login > Desktop Autologin"
id -nG | grep -qw dialout && echo "ok   user can open the eye's mic node (dialout)" \
    || echo "WARN user not in dialout: run 'sudo usermod -aG dialout $USER' and log out/in (needed for the eye)"
pgrep -x swayidle >/dev/null && echo "WARN swayidle running: screen may blank (raspi-config > Display > Screen Blanking)" \
    || echo "ok   no screen blanking"
"$REPO/.venv/bin/python" -m jarvis_terminal --list-devices | grep -qiE "usb|speak|jabra|anker|conference" \
    && echo "ok   USB audio device found" \
    || echo "WARN no USB speakerphone seen; list devices with: $REPO/.venv/bin/python -m jarvis_terminal --list-devices"

echo "Done. Jarvis starts at every login. Now: reboot, or run $PI/kiosk.sh & to start the screen now."
