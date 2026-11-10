#!/usr/bin/env bash
# Installs Dimly from source for the current user, into ~/.local. No sudo needed,
# but your account must already be able to reach monitors: see "Setting up" in
# README.md. Safe to run again after editing the code. uninstall.sh undoes it.
set -euo pipefail

src="$(cd "$(dirname "$0")" && pwd)"
app_id="io.github.saikatnjm.Dimly"
dest="$HOME/.local/share/dimly"
bindir="$HOME/.local/bin"
share="$HOME/.local/share"
units="$HOME/.config/systemd/user"

extension="$share/gnome-shell/extensions/dimly@saikatnjm.github.io"
mkdir -p "$dest" "$bindir" "$units" "$share/applications" "$share/metainfo" \
         "$share/icons/hicolor/scalable/apps" "$(dirname "$extension")"
rm -rf "$extension" && cp -r "$src/extension" "$extension"
install -m 755 "$src/src/dimly.py" "$dest/dimly.py"
find "$src/src" -maxdepth 1 -name "*.py" ! -name dimly.py -exec install -m 644 {} "$dest/" \;
ln -sf "$dest/dimly.py" "$bindir/dimly"

sed "s|@BINDIR@|$bindir|" "$src/data/$app_id.desktop.in" > "$share/applications/$app_id.desktop"
install -m 644 "$src/data/$app_id.metainfo.xml" "$share/metainfo/"
install -m 644 "$src/data/$app_id.svg" "$share/icons/hicolor/scalable/apps/"
sed "s|@BINDIR@|$bindir|" "$src/data/dimly-background.service.in" > "$units/dimly-background.service"
# Versions before 0.3.0 kept the schedule with a timer; the background service does it now.
systemctl --user disable --now dimly-schedule.timer 2>/dev/null || true
rm -f "$units/dimly-schedule.timer" "$units/dimly-schedule.service"
update-desktop-database "$share/applications" 2>/dev/null || true
# No icon cache here: GTK reads ~/.local icons without one, and a cache left
# behind after uninstalling points at the missing icon, hiding the packaged one.

"$bindir/dimly" --setup
systemctl --user restart dimly-background.service

echo "Installed. Open \"Dimly\" from the app menu, or run: dimly"
python3 - "$dest" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
import ddc
problem = ddc.access_problem()
if problem:
    print("Note:", problem)
EOF
