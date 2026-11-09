#!/usr/bin/env bash
# Removes what install.sh added: the app, launcher, background services and shortcuts.
# Your settings in ~/.config/dimly are kept; delete that folder to remove them too.
set -euo pipefail

app_id="io.github.saikatnjm.Dimly"
share="$HOME/.local/share"
units="$HOME/.config/systemd/user"

"$HOME/.local/bin/dimly" --remove-setup 2>/dev/null || true
rm -f "$units/dimly-background.service" "$units/dimly-schedule.service" "$units/dimly-schedule.timer"
systemctl --user daemon-reload

rm -f "$share/applications/$app_id.desktop" "$share/metainfo/$app_id.metainfo.xml" \
      "$share/icons/hicolor/scalable/apps/$app_id.svg" "$HOME/.local/bin/dimly"
rm -rf "$share/dimly" "$HOME/.cache/dimly"
update-desktop-database "$share/applications" 2>/dev/null || true
# Older install.sh versions made an icon cache; refresh it so it stops listing
# the removed icon, which would otherwise hide a packaged Dimly's icon.
if [ -f "$share/icons/hicolor/icon-theme.cache" ]; then
    gtk-update-icon-cache -q -t -f "$share/icons/hicolor" 2>/dev/null || true
fi
echo "Dimly removed."
