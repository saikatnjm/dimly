"""Hooks Dimly into the desktop for the current user: keyboard shortcuts and background services.

These are per-user settings, which a system package can't set, so the window
runs setup() the first time it opens. Nothing here needs root.
"""

import os
import subprocess
import sys

from gi.repository import Gio

import ddc

APP_ID = "io.github.saikatnjm.Dimly"
VERSION = "0.2.0"
WEBSITE = "https://github.com/saikatnjm/dimly"
# (id, name shown in GNOME Settings, option, default keys)
SHORTCUTS = [
    ("dimly-up", "Dimly: brighter", "--up", "<Super>F6"),
    ("dimly-down", "Dimly: dimmer", "--down", "<Super>F5"),
]
UNITS = ["dimly-schedule.timer", "dimly-background.service"]

MEDIA_KEYS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KEYBINDING_SCHEMA = MEDIA_KEYS_SCHEMA + ".custom-keybinding"
KEYBINDING_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/{}/"


def _has_schema(schema_id):
    # Gio.Settings aborts the whole process on a missing schema, so check first.
    return Gio.SettingsSchemaSource.get_default().lookup(schema_id, True) is not None


def _keybinding(shortcut_id):
    return Gio.Settings.new_with_path(KEYBINDING_SCHEMA, KEYBINDING_PATH.format(shortcut_id))


def shortcut_binding(shortcut_id):
    """Return the keys GNOME has for one of Dimly's shortcuts, or "" if there are none."""
    return _keybinding(shortcut_id).get_string("binding") if _has_schema(KEYBINDING_SCHEMA) else ""


def needs_setup():
    return not ddc.load_state().get("setup_done")


def setup():
    """Add the shortcuts and start the background services. Safe to run again:
    keys someone changed in GNOME Settings are kept."""
    if _has_schema(KEYBINDING_SCHEMA):
        command = os.path.realpath(sys.argv[0])
        media_keys = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
        paths = list(media_keys.get_strv("custom-keybindings"))
        for shortcut_id, name, option, keys in SHORTCUTS:
            path = KEYBINDING_PATH.format(shortcut_id)
            binding = _keybinding(shortcut_id)
            binding.set_string("name", name)
            binding.set_string("command", f"{command} {option}")
            if path not in paths:
                binding.set_string("binding", keys)
                paths.append(path)
        media_keys.set_strv("custom-keybindings", paths)
        Gio.Settings.sync()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", *UNITS], check=False, capture_output=True)
    ddc.update_state(setup_done=True)


def remove():
    """Undo setup(): remove the shortcuts and stop the background services."""
    subprocess.run(["systemctl", "--user", "disable", "--now", *UNITS], check=False, capture_output=True)
    if _has_schema(KEYBINDING_SCHEMA):
        mine = [KEYBINDING_PATH.format(shortcut_id) for shortcut_id, *_ in SHORTCUTS]
        media_keys = Gio.Settings.new(MEDIA_KEYS_SCHEMA)
        media_keys.set_strv("custom-keybindings",
                            [p for p in media_keys.get_strv("custom-keybindings") if p not in mine])
        for shortcut_id, *_ in SHORTCUTS:
            binding = _keybinding(shortcut_id)
            for key in ("name", "command", "binding"):
                binding.reset(key)
        Gio.Settings.sync()
    ddc.update_state(setup_done=False)
