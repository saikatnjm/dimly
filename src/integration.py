"""Hooks Dimly into the desktop for the current user: keyboard shortcuts and background services.

These are per-user settings, which a system package can't set, so the window
runs setup() the first time it opens. Nothing here needs root.
"""

import os
import shlex
import shutil
import subprocess
import sys

from gi.repository import Gio, GLib

import ddc

# (id, name shown in GNOME Settings, option, default keys)
SHORTCUTS = [
    ("dimly-up", "Dimly: brighter", "--up", "<Super>F6"),
    ("dimly-down", "Dimly: dimmer", "--down", "<Super>F5"),
]
UNITS = ["dimly-background.service"]
# Versions before 0.3.0 also enabled this timer; the service now keeps the schedule.
OLD_UNITS = ["dimly-schedule.timer"]
EXTENSION_UUID = "dimly@saikatnjm.github.io"
# Bumped when setup() gains a step, so existing installs run it once more.
# 1: shortcuts and background services. 2: the top-bar menu.
SETUP_VERSION = 2

SHELL_SCHEMA = "org.gnome.shell"
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


def _setup_version():
    state = ddc.load_state()
    # Before versions were recorded, setup only noted that it was done.
    return state.get("setup_version", 1 if state.get("setup_done") else 0)


def needs_setup():
    return _setup_version() < SETUP_VERSION


def extension_installed():
    """Whether the top-bar extension's files are on this computer, for GNOME to load."""
    data_dirs = [GLib.get_user_data_dir(), *GLib.get_system_data_dirs()]
    return any(os.path.isdir(os.path.join(d, "gnome-shell", "extensions", EXTENSION_UUID)) for d in data_dirs)


def top_bar_enabled():
    if not _has_schema(SHELL_SCHEMA):
        return False
    return EXTENSION_UUID in Gio.Settings.new(SHELL_SCHEMA).get_strv("enabled-extensions")


def set_top_bar(enabled):
    """Turn the top-bar menu on or off. GNOME picks this up straight away, once it has
    loaded the extension; a just-installed one needs you to log out and back in first."""
    if not _has_schema(SHELL_SCHEMA):
        return
    shell = Gio.Settings.new(SHELL_SCHEMA)
    for key, wanted in (("enabled-extensions", enabled), ("disabled-extensions", not enabled)):
        uuids = [u for u in shell.get_strv(key) if u != EXTENSION_UUID]
        shell.set_strv(key, uuids + [EXTENSION_UUID] if wanted else uuids)
    Gio.Settings.sync()


def gnome_has_loaded_extension():
    """Whether the running GNOME Shell knows the top-bar extension. It only looks for
    new extensions at login on Wayland, so a fresh install needs a log out first."""
    try:
        reply = Gio.bus_get_sync(Gio.BusType.SESSION).call_sync(
            "org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions",
            "GetExtensionInfo", GLib.Variant("(s)", (EXTENSION_UUID,)), GLib.VariantType("(a{sv})"),
            Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        return False
    return bool(reply.unpack()[0])


def setup():
    """Add the shortcuts, start the background services and show the top-bar menu.
    Safe to run again: keys someone changed in GNOME Settings are kept, and the
    top-bar menu is only switched on the first time, so turning it off sticks."""
    if _setup_version() < 2 and extension_installed():
        set_top_bar(True)
    if _has_schema(KEYBINDING_SCHEMA):
        # GNOME splits the command like a shell does, so quote it: paths can have spaces.
        command = shlex.quote(shutil.which("dimly") or os.path.realpath(sys.argv[0]))
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
    ddc.update_state(setup_done=True, setup_version=SETUP_VERSION)


def remove():
    """Undo setup(): remove the shortcuts and the top-bar menu, and stop the background services."""
    subprocess.run(["systemctl", "--user", "disable", "--now", *UNITS, *OLD_UNITS], check=False, capture_output=True)
    if _has_schema(SHELL_SCHEMA):
        shell = Gio.Settings.new(SHELL_SCHEMA)
        for key in ("enabled-extensions", "disabled-extensions"):
            shell.set_strv(key, [u for u in shell.get_strv(key) if u != EXTENSION_UUID])
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
    ddc.update_state(setup_done=False, setup_version=0)
