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

mkdir -p "$dest" "$bindir" "$units" "$share/applications" "$share/metainfo" \
         "$share/icons/hicolor/scalable/apps"
install -m 755 "$src/src/dimly.py" "$dest/dimly.py"
install -m 644 "$src/src/ddc.py" "$src/src/gui.py" "$src/src/laptop.py" "$src/src/integration.py" "$dest/"
ln -sf "$dest/dimly.py" "$bindir/dimly"

sed "s|@BINDIR@|$bindir|" "$src/data/$app_id.desktop.in" > "$share/applications/$app_id.desktop"
install -m 644 "$src/data/$app_id.metainfo.xml" "$share/metainfo/"
install -m 644 "$src/data/$app_id.svg" "$share/icons/hicolor/scalable/apps/"
for unit in dimly-background.service dimly-schedule.service; do
    sed "s|@BINDIR@|$bindir|" "$src/data/$unit.in" > "$units/$unit"
done
install -m 644 "$src/data/dimly-schedule.timer" "$units/"
update-desktop-database "$share/applications" 2>/dev/null || true
# No icon cache here: GTK reads ~/.local icons without one, and a cache left
# behind after uninstalling points at the missing icon, hiding the packaged one.

"$bindir/dimly" --setup
systemctl --user restart dimly-background.service

echo "Installed. Open \"Dimly\" from the app menu, or run: dimly"
python3 -c "import sys; sys.path.insert(0, '$dest'); import ddc; p = ddc.access_problem(); print('Note:', p) if p else None"
