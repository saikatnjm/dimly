#!/usr/bin/env python3
"""Dimly: easy-on-the-eyes brightness for external monitors, in step with your laptop screen.

Run without options to open the window. The options are what the keyboard
shortcuts, the schedule timer and the background service run.
"""

import argparse
import sys
from datetime import datetime

import ddc
import integration


def notify(summary, body=""):
    """Show a short-lived notification, replacing the previous one so key presses don't pile up."""
    from gi.repository import Gio, GLib

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        reply = bus.call_sync(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify",
            GLib.Variant("(susssasa{sv}i)", (
                "Dimly", ddc.load_state().get("notification_id", 0), integration.APP_ID,
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


def change(new_value, title):
    results = ddc.set_brightness(new_value)
    if not results:
        notify("No adjustable monitor found", "Check the cable, and that DDC/CI is on in the monitor's menu.")
        return 1
    value, maximum = results[0]
    notify(f"{title} {round(100 * value / maximum)}%", level_bar(value, maximum))
    return 0


def apply_schedule():
    """Apply the scheduled preset once per time slot, so a change made by hand lasts until the next slot."""
    config = ddc.load_config()
    slot = ddc.current_slot(config["schedule"], datetime.now()) if config["schedule_enabled"] else None
    if slot is None or ddc.load_state().get("applied_slot") == slot[0]:
        return 0
    key, preset = slot
    # When no monitor answers, leave the slot unapplied so the next run tries again.
    if ddc.set_brightness(ddc.level(ddc.presets()[preset])):
        ddc.update_state(applied_slot=key)
        notify(f"{preset} brightness", f"Switched by your schedule at {key.split()[1]}")
    return 0


def main():
    parser = argparse.ArgumentParser(prog="dimly", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {integration.VERSION}")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--up", action="store_true", help="brighten by one step")
    action.add_argument("--down", action="store_true", help="dim by one step")
    action.add_argument("--set", type=int, metavar="LEVEL", help="set brightness to LEVEL, from 0 to 100")
    action.add_argument("--preset", choices=ddc.PRESET_NAMES, help="switch to a preset")
    action.add_argument("--apply-schedule", action="store_true",
                        help="apply the scheduled preset if a new time slot has started")
    action.add_argument("--background", action="store_true",
                        help="run the background service: follow the laptop screen, add new monitors, serve the top-bar menu")
    action.add_argument("--setup", action="store_true",
                        help="add the keyboard shortcuts and start the background services")
    action.add_argument("--remove-setup", action="store_true", help="undo --setup")
    args = parser.parse_args()

    if args.up or args.down:
        step = ddc.load_config()["step"] * (1 if args.up else -1)
        return change(lambda current, _: current + step, "Brightness")
    if args.set is not None:
        return change(ddc.level(args.set), "Brightness")
    if args.preset:
        return change(ddc.level(ddc.presets()[args.preset]), args.preset)
    if args.apply_schedule:
        return apply_schedule()
    if args.background:
        import service
        return service.run()
    if args.setup or args.remove_setup:
        integration.setup() if args.setup else integration.remove()
        return 0

    import gui
    return gui.run()


if __name__ == "__main__":
    sys.exit(main())
