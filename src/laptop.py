"""The laptop's built-in screen brightness, through GNOME's power daemon (no root needed)."""

from gi.repository import Gio, GLib

BUS_NAME = "org.gnome.SettingsDaemon.Power"
PATH = "/org/gnome/SettingsDaemon/Power"
IFACE = "org.gnome.SettingsDaemon.Power.Screen"
PROPERTIES = "org.freedesktop.DBus.Properties"
# Some panels go fully black at 0, which looks like a broken screen.
MINIMUM = 5


_connection = None


def session_bus():
    # GLib only keeps the shared connection while someone holds it; without this
    # reference it closes, dropping signal subscriptions and unfinished calls.
    global _connection
    if _connection is None:
        _connection = Gio.bus_get_sync(Gio.BusType.SESSION)
    return _connection


def get_brightness():
    """Return the laptop screen's brightness in percent, or None if there's no adjustable screen."""
    try:
        reply = session_bus().call_sync(BUS_NAME, PATH, PROPERTIES, "Get", GLib.Variant("(ss)", (IFACE, "Brightness")),
                                        GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        return None
    value = reply.unpack()[0]
    return value if value >= 0 else None  # GNOME reports -1 when there's no backlight


def set_brightness(percent, wait=True):
    """Set the laptop screen's brightness in percent. With wait=False, don't block (for the window)."""
    args = GLib.Variant("(ssv)", (IFACE, "Brightness", GLib.Variant("i", max(MINIMUM, min(100, percent)))))
    try:
        if wait:
            session_bus().call_sync(BUS_NAME, PATH, PROPERTIES, "Set", args, None, Gio.DBusCallFlags.NONE, 2000, None)
        else:
            session_bus().call(BUS_NAME, PATH, PROPERTIES, "Set", args, None, Gio.DBusCallFlags.NONE, 2000, None, None)
    except GLib.Error:
        pass  # no laptop screen to follow


def watch(callback):
    """Call callback(percent) whenever the laptop screen's brightness changes, e.g. from its keys."""
    def on_signal(_conn, _sender, _path, _iface, _signal, params):
        _, changed, _ = params.unpack()
        if "Brightness" in changed:
            callback(changed["Brightness"])

    return session_bus().signal_subscribe(BUS_NAME, PROPERTIES, "PropertiesChanged", PATH, IFACE,
                                          Gio.DBusSignalFlags.NONE, on_signal)
