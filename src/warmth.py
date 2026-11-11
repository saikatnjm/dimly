"""Screen warmth for every screen, from 0 (off) to 100 (warmest).

Where the desktop has a night light (GNOME, newer Cinnamon), warmth is its
setting. The night light either keeps its own hours (sunset to sunrise, or ones
you set in the desktop's Settings) or stays on all the time; switching back
restores its own hours.

Without one (e.g. older Linux Mint), Dimly tints the screens itself on X11, by
setting their colour ramps as redshift does, through the X libraries every X11
desktop has. That lasts until the next log in, so the background service puts
it back then, and after waking up or plugging in a screen.
"""

import ctypes
import os

from gi.repository import Gio, GLib

import ddc
import laptop

# GNOME and Cinnamon use the same keys under different names.
SCHEMAS = {"gnome": "org.gnome.settings-daemon.plugins.color",
           "cinnamon": "org.cinnamon.settings-daemon.plugins.color"}
COOLEST, WARMEST = 6500, 1700  # colour temperatures in kelvin, for warmth 0 and 100
# The colour of white at 1500 K, 2000 K, ... 6500 K, as (red, green, blue), from redshift's table.
WHITE_POINTS = [(1.0, 0.42322816, 0.0), (1.0, 0.54360078, 0.08679949), (1.0, 0.64373109, 0.28819679),
                (1.0, 0.71976951, 0.42860152), (1.0, 0.77987699, 0.54642268), (1.0, 0.82854786, 0.64816570),
                (1.0, 0.86860704, 0.73688797), (1.0, 0.90198230, 0.81465502), (1.0, 0.93853986, 0.88130458),
                (1.0, 0.97107439, 0.94305985), (1.0, 1.0, 1.0)]
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


def white_point(kelvin):
    """The (red, green, blue) multipliers that tint white to this colour temperature."""
    position = (max(1500, min(6500, kelvin)) - 1500) / 500
    low = WHITE_POINTS[int(position)]
    high = WHITE_POINTS[min(int(position) + 1, len(WHITE_POINTS) - 1)]
    fraction = position - int(position)
    return tuple(a + (b - a) * fraction for a, b in zip(low, high))


def get_warmth(night_light):
    if not night_light.get_boolean("night-light-enabled"):
        return 0
    return from_kelvin(night_light.get_uint("night-light-temperature"))


def set_warmth(night_light, level):
    if level <= 0:
        night_light.set_boolean("night-light-enabled", False)
    else:
        night_light.set_uint("night-light-temperature", to_kelvin(level))
        night_light.set_boolean("night-light-enabled", True)
    Gio.Settings.sync()  # written before a short-lived process, like a keyboard shortcut, ends


# Either way


def available():
    """Whether warmth can be changed on this desktop."""
    return settings() is not None or _x11() is not None


def get_level():
    """The current warmth, 0 to 100, or None where it can't be changed."""
    night_light = settings()
    if night_light:
        return get_warmth(night_light)
    level = ddc.load_state().get("warmth", 0)
    return level if _x11() and type(level) is int else None


def set_level(level, now=False):
    """Set the warmth, 0 to 100, where it can be changed.

    With now, as presets do, the night light shows it at once rather than
    waiting for its own hours.
    """
    level = max(0, min(100, int(level)))
    night_light = settings()
    if night_light:
        if now and level > 0:
            set_when(night_light, "always")
        set_warmth(night_light, level)
    elif _set_ramps(level):
        ddc.update_state(warmth=level)


def restore():
    """Put Dimly's own tint back after a log in, waking up or a new screen. The night light does this itself."""
    level = ddc.load_state().get("warmth", 0)
    if settings() is None and type(level) is int and level > 0:
        _set_ramps(level)


# Dimly's own tint, through Xlib and XRandR


class _Resources(ctypes.Structure):
    _fields_ = [("timestamp", ctypes.c_ulong), ("config_timestamp", ctypes.c_ulong),
                ("ncrtc", ctypes.c_int), ("crtcs", ctypes.POINTER(ctypes.c_ulong))]  # the rest isn't needed


class _Gamma(ctypes.Structure):
    _fields_ = [("size", ctypes.c_int), ("red", ctypes.POINTER(ctypes.c_ushort)),
                ("green", ctypes.POINTER(ctypes.c_ushort)), ("blue", ctypes.POINTER(ctypes.c_ushort))]


_libraries = []


def _x11():
    """(Xlib, XRandR) on an X11 session, or None, e.g. on Wayland."""
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not os.environ.get("DISPLAY"):
        return None
    if not _libraries:
        try:
            x, xrandr = ctypes.CDLL("libX11.so.6"), ctypes.CDLL("libXrandr.so.2")
        except OSError:
            _libraries.append(None)
            return None
        pointer = ctypes.c_void_p
        x.XOpenDisplay.restype = pointer
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [pointer]
        x.XCloseDisplay.argtypes = [pointer]
        xrandr.XRRGetScreenResourcesCurrent.restype = ctypes.POINTER(_Resources)
        xrandr.XRRGetScreenResourcesCurrent.argtypes = [pointer, ctypes.c_ulong]
        xrandr.XRRFreeScreenResources.argtypes = [ctypes.POINTER(_Resources)]
        xrandr.XRRGetCrtcGammaSize.argtypes = [pointer, ctypes.c_ulong]
        xrandr.XRRAllocGamma.restype = ctypes.POINTER(_Gamma)
        xrandr.XRRAllocGamma.argtypes = [ctypes.c_int]
        xrandr.XRRSetCrtcGamma.argtypes = [pointer, ctypes.c_ulong, ctypes.POINTER(_Gamma)]
        xrandr.XRRFreeGamma.argtypes = [ctypes.POINTER(_Gamma)]
        _libraries.append((x, xrandr))
    return _libraries[0]


def ramp(size, multiplier):
    """One colour's ramp: straight from black to this share of full brightness."""
    return [round(65535 * multiplier * i / (size - 1)) for i in range(size)] if size > 1 else [0] * size


def _set_ramps(level):
    """Tint every screen to this warmth. Returns whether it could."""
    libraries = _x11()
    display = libraries and libraries[0].XOpenDisplay(None)
    if not display:
        return False
    x, xrandr = libraries
    red, green, blue = white_point(to_kelvin(level))
    try:
        resources = xrandr.XRRGetScreenResourcesCurrent(display, x.XDefaultRootWindow(display))
        if not resources:
            return False
        try:
            for crtc in resources.contents.crtcs[:resources.contents.ncrtc]:
                size = xrandr.XRRGetCrtcGammaSize(display, crtc)
                if size < 2:
                    continue
                gamma = xrandr.XRRAllocGamma(size)
                for channel, multiplier in (("red", red), ("green", green), ("blue", blue)):
                    values = (ctypes.c_ushort * size)(*ramp(size, multiplier))
                    ctypes.memmove(getattr(gamma.contents, channel), values, ctypes.sizeof(values))
                xrandr.XRRSetCrtcGamma(display, crtc, gamma)
                xrandr.XRRFreeGamma(gamma)
        finally:
            xrandr.XRRFreeScreenResources(resources)
    finally:
        x.XCloseDisplay(display)  # also sends the changes
    return True


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
