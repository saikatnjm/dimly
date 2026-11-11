"""Screen warmth, through the desktop's own night light, so it covers every screen.

Warmth runs from 0 (night light off) to 100 (warmest). The night light either
keeps its own hours (sunset to sunrise, or ones you set in the desktop's Settings)
or stays on all the time; switching back restores its own hours.
"""

import os

from gi.repository import Gio, GLib

import ddc
import laptop

# GNOME and Cinnamon use the same keys under different names.
SCHEMAS = {"gnome": "org.gnome.settings-daemon.plugins.color",
           "cinnamon": "org.cinnamon.settings-daemon.plugins.color"}
COOLEST, WARMEST = 6500, 1700  # colour temperatures in kelvin, for warmth 0 and 100
WHEN = ["usual", "always"]
SCHEDULE_KEYS = ("night-light-schedule-automatic", "night-light-schedule-from", "night-light-schedule-to")


def _desktop():
    return "cinnamon" if "cinnamon" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() else "gnome"


def settings():
    """The night light's settings, or None on a desktop without one."""
    source = Gio.SettingsSchemaSource.get_default()
    schema = source.lookup(SCHEMAS[_desktop()], True) if source else None
    if schema is None or not schema.has_key("night-light-temperature"):
        return None
    return Gio.Settings.new(SCHEMAS[_desktop()])


def to_kelvin(level):
    return round(COOLEST - (COOLEST - WARMEST) * max(0, min(100, level)) / 100)


def from_kelvin(kelvin):
    return max(1, min(100, round((COOLEST - kelvin) * 100 / (COOLEST - WARMEST))))


def get_warmth(night_light):
    if not night_light.get_boolean("night-light-enabled"):
        return 0
    return from_kelvin(night_light.get_uint("night-light-temperature"))


def set_warmth(night_light, level):
    if level <= 0:
        night_light.set_boolean("night-light-enabled", False)
        return
    night_light.set_uint("night-light-temperature", to_kelvin(level))
    night_light.set_boolean("night-light-enabled", True)


def _when(automatic, start, end):
    # The night light treats equal start and end hours as the whole day.
    return "always" if not automatic and start == end else "usual"


def _schedule(night_light):
    return [night_light.get_boolean(SCHEDULE_KEYS[0]),
            night_light.get_double(SCHEDULE_KEYS[1]), night_light.get_double(SCHEDULE_KEYS[2])]


def get_when(night_light):
    return _when(*_schedule(night_light))


def set_when(night_light, when):
    if when == get_when(night_light):
        return
    if when == "always":
        # Remember the night light's own hours, to put them back later.
        ddc.update_state(night_light_schedule=_schedule(night_light))
        night_light.set_boolean(SCHEDULE_KEYS[0], False)
        night_light.set_double(SCHEDULE_KEYS[1], 0.0)
        night_light.set_double(SCHEDULE_KEYS[2], 0.0)
        return
    saved = ddc.load_state().get("night_light_schedule")
    if isinstance(saved, list) and len(saved) == 3 and _when(*saved) == "usual":
        night_light.set_double(SCHEDULE_KEYS[1], float(saved[1]))
        night_light.set_double(SCHEDULE_KEYS[2], float(saved[2]))
        night_light.set_boolean(SCHEDULE_KEYS[0], bool(saved[0]))
    else:
        night_light.set_boolean(SCHEDULE_KEYS[0], True)  # nothing saved: sunset to sunrise


def preview(seconds=3):
    """Show the chosen warmth for a moment, even when the night light isn't on yet (GNOME only)."""
    if _desktop() != "gnome":
        return
    laptop.session_bus().call(  # the shared connection, kept open until the call is done
        "org.gnome.SettingsDaemon.Color", "/org/gnome/SettingsDaemon/Color", "org.gnome.SettingsDaemon.Color",
        "NightLightPreview", GLib.Variant("(u)", (seconds,)), None, Gio.DBusCallFlags.NONE, 2000, None, None)
