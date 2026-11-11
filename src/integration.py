"""Hooks Dimly into the desktop for the current user: keyboard shortcuts, the
top-bar menu and the background service.

These are per-user settings, which a system package can't set, so the window
runs setup() the first time it opens. Nothing here needs root.
"""

import os
import re
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
# The top-bar menu comes in two forms, because GNOME 45 changed how extensions
# are written. Both ship with Dimly, next to this file. The .deb links the one
# GNOME needs into GNOME's own folder (and again whenever GNOME is upgraded);
# a source install in your home folder gets that link in your account instead.
EXTENSIONS_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "extension")
USER_EXTENSION = os.path.join(GLib.get_user_data_dir(), "gnome-shell", "extensions", EXTENSION_UUID)
# Bumped when setup() gains a step, so existing installs run it once more.
# 1: shortcuts and background services. 2: the top-bar menu. 3: the menu linked
# per GNOME version (before, the package installed one for GNOME 42 only).
SETUP_VERSION = 3
# GNOME's extension states, the same numbers in GNOME 42 and later.
EXTENSION_ENABLED, EXTENSION_ERROR, EXTENSION_OUT_OF_DATE = 1, 3, 4

SHELL_SCHEMA = "org.gnome.shell"
# On Cinnamon (Linux Mint) the menu is a panel applet instead, with the same UUID.
CINNAMON_SCHEMA = "org.cinnamon"
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


def gnome_version():
    """The major version of GNOME Shell, or None outside GNOME."""
    try:
        reply = Gio.bus_get_sync(Gio.BusType.SESSION).call_sync(
            "org.gnome.Shell", "/org/gnome/Shell", "org.freedesktop.DBus.Properties", "Get",
            GLib.Variant("(ss)", ("org.gnome.Shell", "ShellVersion")), GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NONE, 2000, None)
        text = reply.unpack()[0]
    except GLib.Error:
        try:  # GNOME isn't running, e.g. over SSH: ask the installed one
            text = subprocess.run(["gnome-shell", "--version"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return None
    match = re.search(r"(\d+)(?:\.\w+)*\s*$", text.strip())
    return int(match.group(1)) if match else None


def bundled_extension():
    """The folder of the top-bar menu this GNOME needs, or None if there's none for it."""
    version = gnome_version()
    if version is None or version < 42:
        return None
    path = os.path.join(EXTENSIONS_DIR, "gnome45" if version >= 45 else "gnome42")
    return path if os.path.isdir(path) else None


def on_cinnamon():
    return "cinnamon" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def panel_name():
    """What the desktop calls the bar Dimly's menu lives in."""
    return "panel" if on_cinnamon() else "top bar"


def _applet_installed():
    data_dirs = [GLib.get_user_data_dir(), *GLib.get_system_data_dirs()]
    return any(os.path.isdir(os.path.join(d, "cinnamon", "applets", EXTENSION_UUID)) for d in data_dirs)


def _is_applet(entry):
    # Cinnamon lists applets as "panel:zone:order:uuid:instance".
    return entry.split(":")[3:4] == [EXTENSION_UUID]


def applet_entries(applets, enabled, zone, panel, next_id):
    """Cinnamon's applet list with Dimly's applet in `zone`, or without it, and the
    next free applet ID. An applet already placed keeps its panel and ID, so moving
    it doesn't pull it off a panel you chose; a new one goes on `panel` and takes
    Cinnamon's next free ID, as Cinnamon's own "Add applet" does."""
    mine = [e.split(":") for e in applets if _is_applet(e)]
    entries = [e for e in applets if not _is_applet(e)]
    if enabled:
        if mine:
            old_panel, old_zone, order, _, instance = (mine[0] + [""] * 5)[:5]
            entries.append(f"{old_panel}:{zone}:{order if zone == old_zone else 0}:{EXTENSION_UUID}:{instance}")
        else:
            used = [int(e.split(":")[4]) for e in applets if e.count(":") >= 4 and e.split(":")[4].isdigit()]
            instance = max(next_id, max(used, default=0) + 1)
            entries.append(f"{panel}:{zone}:0:{EXTENSION_UUID}:{instance}")
            next_id = instance + 1
    return entries, next_id


def _place_applet(enabled):
    settings = Gio.Settings.new(CINNAMON_SCHEMA)
    # Panels are listed as "id:monitor:position"; the first is the main one.
    panels = settings.get_strv("panels-enabled")
    panel = f"panel{panels[0].split(':')[0]}" if panels else "panel1"
    entries, next_id = applet_entries(list(settings.get_strv("enabled-applets")), enabled,
                                      ddc.load_config()["top_bar_position"], panel, settings.get_int("next-applet-id"))
    settings.set_strv("enabled-applets", entries)
    settings.set_int("next-applet-id", next_id)
    Gio.Settings.sync()


def top_bar_available():
    if on_cinnamon():
        return _has_schema(CINNAMON_SCHEMA) and _applet_installed()
    return bundled_extension() is not None


def apply_position():
    """Move the menu after the position setting changed. The GNOME extension watches
    the settings file itself; Cinnamon's panel needs its applet entry rewritten."""
    if on_cinnamon() and top_bar_enabled():
        _place_applet(True)


def _installed_from_source():
    return os.path.realpath(EXTENSIONS_DIR).startswith(os.path.realpath(GLib.get_user_data_dir()) + os.sep)


def link_extension():
    """For a source install, point your account's copy of the top-bar menu at the
    version this GNOME needs. Returns True if that changed, in which case GNOME
    needs a log out to load it. The .deb keeps its own link up to date."""
    target = bundled_extension()
    if target is None or not _installed_from_source():
        return False
    if os.path.islink(USER_EXTENSION) and os.path.realpath(USER_EXTENSION) == os.path.realpath(target):
        return False
    if os.path.islink(USER_EXTENSION) or os.path.isfile(USER_EXTENSION):
        os.unlink(USER_EXTENSION)
    elif os.path.isdir(USER_EXTENSION):
        shutil.rmtree(USER_EXTENSION)  # a copy made by install.sh before 0.4.0
    os.makedirs(os.path.dirname(USER_EXTENSION), exist_ok=True)
    os.symlink(target, USER_EXTENSION)
    return True


def unlink_extension():
    if _installed_from_source() and os.path.islink(USER_EXTENSION):
        os.unlink(USER_EXTENSION)


def top_bar_enabled():
    if on_cinnamon():
        return _has_schema(CINNAMON_SCHEMA) and any(
            _is_applet(e) for e in Gio.Settings.new(CINNAMON_SCHEMA).get_strv("enabled-applets"))
    if not _has_schema(SHELL_SCHEMA):
        return False
    return EXTENSION_UUID in Gio.Settings.new(SHELL_SCHEMA).get_strv("enabled-extensions")


def set_top_bar(enabled):
    """Turn the top-bar menu on or off. GNOME picks this up straight away, once it has
    loaded the extension; a just-installed one needs you to log out and back in first."""
    if on_cinnamon():
        if _has_schema(CINNAMON_SCHEMA):
            _place_applet(enabled)
        return
    if not _has_schema(SHELL_SCHEMA):
        return
    if enabled:
        link_extension()
    shell = Gio.Settings.new(SHELL_SCHEMA)
    for key, wanted in (("enabled-extensions", enabled), ("disabled-extensions", not enabled)):
        uuids = [u for u in shell.get_strv(key) if u != EXTENSION_UUID]
        shell.set_strv(key, uuids + [EXTENSION_UUID] if wanted else uuids)
    Gio.Settings.sync()


def _extension_info():
    """What the running GNOME Shell knows about the top-bar extension; {} if nothing.
    It only looks for new extensions at login on Wayland."""
    try:
        reply = Gio.bus_get_sync(Gio.BusType.SESSION).call_sync(
            "org.gnome.Shell", "/org/gnome/Shell", "org.gnome.Shell.Extensions",
            "GetExtensionInfo", GLib.Variant("(s)", (EXTENSION_UUID,)), GLib.VariantType("(a{sv})"),
            Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        return {}
    return reply.unpack()[0]


def _extensions_switched_off():
    # The main switch in GNOME's Extensions app turns every extension off.
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(SHELL_SCHEMA, True) if source else None
    return bool(schema and schema.has_key("disable-user-extensions")
                and Gio.Settings.new(SHELL_SCHEMA).get_boolean("disable-user-extensions"))


def top_bar_status():
    """A short line saying what GNOME is doing with the top-bar menu, for the window."""
    if not top_bar_enabled():
        return "Brightness, contrast, warmth and presets, one click away"
    if on_cinnamon():
        return "Showing in the panel"
    if _extensions_switched_off():
        return "Extensions are switched off in GNOME: turn them on in the Extensions app"
    info = _extension_info()
    state = info.get("state")
    if state == EXTENSION_ENABLED:
        return "Showing in the top bar"
    if state in (EXTENSION_OUT_OF_DATE, EXTENSION_ERROR) and bundled_extension():
        # GNOME still remembers the menu from before an upgrade; it rereads it at login.
        return "Log out and back in to load the updated menu"
    if state == EXTENSION_OUT_OF_DATE:
        return f"Not supported on GNOME {gnome_version()} yet"
    if state == EXTENSION_ERROR:
        return f"GNOME couldn't load it: {info.get('error') or 'unknown error'}"
    return "Log out and back in once to see it"


def setup():
    """Add the shortcuts, start the background services and show the top-bar menu.
    Safe to run again: keys someone changed in GNOME Settings are kept, and the
    top-bar menu is only switched on the first time, so turning it off sticks."""
    previous = _setup_version()
    # Mint got its panel menu in setup 3, so earlier Mint installs never had one to turn off.
    if (previous < 2 or (previous < 3 and on_cinnamon())) and top_bar_available():
        set_top_bar(True)
    elif top_bar_enabled():
        link_extension()
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
    unlink_extension()
    if on_cinnamon() and _has_schema(CINNAMON_SCHEMA):
        _place_applet(False)
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
