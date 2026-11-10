#!/usr/bin/env bash
# Builds dist/dimly_<version>_all.deb. Needs only dpkg-deb; no root.
# Unlike install.sh, the package also sets up monitor access (the i2c-dev
# module and a udev rule), so users don't have to.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
app_id="io.github.saikatnjm.Dimly"
version="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$root/src/about.py")"
[ -n "$version" ] || { echo "No VERSION found in src/about.py" >&2; exit 1; }
stage="$root/build/dimly_${version}_all"

rm -rf "$stage"
mkdir -p "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/share/dimly" "$stage/usr/share/applications" \
         "$stage/usr/share/metainfo" "$stage/usr/share/icons/hicolor/scalable/apps" \
         "$stage/usr/share/doc/dimly" "$stage/usr/lib/systemd/user" "$stage/usr/lib/udev/rules.d" \
         "$stage/usr/lib/modules-load.d"

install -m 755 "$root/src/dimly.py" "$stage/usr/share/dimly/"
find "$root/src" -maxdepth 1 -name "*.py" ! -name dimly.py -exec install -m 644 {} "$stage/usr/share/dimly/" \;
extension="$stage/usr/share/gnome-shell/extensions/dimly@saikatnjm.github.io"
mkdir -p "$extension/icons"
install -m 644 "$root"/extension/*.js "$root"/extension/*.json "$root"/extension/*.css "$extension/"
install -m 644 "$root"/extension/icons/*.svg "$extension/icons/"
ln -s ../share/dimly/dimly.py "$stage/usr/bin/dimly"

sed "s|@BINDIR@|/usr/bin|" "$root/data/$app_id.desktop.in" > "$stage/usr/share/applications/$app_id.desktop"
install -m 644 "$root/data/$app_id.metainfo.xml" "$stage/usr/share/metainfo/"
install -m 644 "$root/data/$app_id.svg" "$stage/usr/share/icons/hicolor/scalable/apps/"
# The schedule units are kept only so installs from before 0.3.0 can switch the
# old timer off (the background service does that); drop them in a later release.
for unit in dimly-background.service dimly-schedule.service; do
    sed "s|@BINDIR@|/usr/bin|" "$root/data/$unit.in" > "$stage/usr/lib/systemd/user/$unit"
done
install -m 644 "$root/data/dimly-schedule.timer" "$stage/usr/lib/systemd/user/"
install -m 644 "$root/data/60-dimly-i2c.rules" "$stage/usr/lib/udev/rules.d/"
install -m 644 "$root/data/dimly-i2c.conf" "$stage/usr/lib/modules-load.d/dimly.conf"
install -m 644 "$root/LICENSE" "$stage/usr/share/doc/dimly/copyright"
chmod 644 "$stage"/usr/share/applications/* "$stage"/usr/lib/systemd/user/*
# Folders get the build machine's umask otherwise; packages must use 755.
find "$stage" -type d -exec chmod 755 {} +

cat > "$stage/DEBIAN/control" <<EOF
Package: dimly
Version: $version
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, ddcutil
Recommends: gnome-shell
Maintainer: Saikat Roy <njmsaikat@gmail.com>
Homepage: https://github.com/saikatnjm/dimly
Description: Easy-on-the-eyes brightness for external monitors
 Dimly controls your external monitor's backlight over DDC/CI, keeps it in
 step with your laptop screen, and dims both on a schedule. It adds keyboard
 shortcuts, follows the laptop's brightness keys and picks up newly connected
 monitors automatically.
EOF

cat > "$stage/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    modprobe i2c-dev 2>/dev/null || true
    if command -v udevadm >/dev/null; then
        udevadm control --reload-rules || true
        udevadm trigger --subsystem-match=i2c-dev || true
    fi
fi
EOF
chmod 755 "$stage/DEBIAN/postinst"

mkdir -p "$root/dist"
dpkg-deb --root-owner-group --build "$stage" "$root/dist/dimly_${version}_all.deb"
