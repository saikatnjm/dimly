"""Short-lived desktop notifications for brightness changes."""

from gi.repository import Gio, GLib

import ddc
from about import APP_ID


def notify(summary, body=""):
    """Show a notification, replacing the previous one so key presses don't pile up."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        reply = bus.call_sync(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify",
            GLib.Variant("(susssasa{sv}i)", (
                "Dimly", ddc.load_state().get("notification_id", 0), APP_ID,
                summary, body, [], {"transient": GLib.Variant("b", True)}, 1500,
            )),
            GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 2000, None,
        )
        ddc.update_state(notification_id=reply.unpack()[0])
    except GLib.Error:
        pass  # a missing notification is no reason to fail the change itself


def level_bar(value, maximum):
    filled = round(10 * value / maximum)
    return "▮" * filled + "▯" * (10 - filled)
