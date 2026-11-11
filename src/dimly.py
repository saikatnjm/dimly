#!/usr/bin/env python3
"""Dimly: easy-on-the-eyes brightness for external monitors, in step with your laptop screen.

Run without options to open the window. The options are what the keyboard
shortcuts and the background service run.
"""

import argparse
import sys

import about
import ddc


def change(apply, title):
    """Run apply(), which returns set_brightness's results, and show how it went."""
    from notify import level_bar, notify

    results = apply()
    if not results:
        notify("No adjustable monitor found", "Check the cable, and that DDC/CI is on in the monitor's menu.")
        return 1
    value, maximum = results[0]
    notify(f"{title} {round(100 * value / maximum)}%", level_bar(value, maximum))
    return 0


def main():
    parser = argparse.ArgumentParser(prog="dimly", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {about.VERSION}")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--up", action="store_true", help="brighten by one step")
    action.add_argument("--down", action="store_true", help="dim by one step")
    action.add_argument("--set", type=int, metavar="LEVEL", help="set brightness to LEVEL, from 0 to 100")
    action.add_argument("--preset", choices=ddc.PRESET_NAMES,
                        help="switch to a preset: its brightness, contrast and warmth")
    action.add_argument("--apply-schedule", action="store_true",
                        help="apply the scheduled preset if a new time slot has started "
                             "(the background service does this by itself)")
    action.add_argument("--background", action="store_true",
                        help="run the background service: follow the laptop screen, add new monitors, serve the top-bar menu")
    action.add_argument("--setup", action="store_true",
                        help="add the keyboard shortcuts, the top-bar menu and the background service")
    action.add_argument("--remove-setup", action="store_true", help="undo --setup")
    args = parser.parse_args()

    if args.up or args.down:
        step = ddc.load_config()["step"] * (1 if args.up else -1)
        return change(lambda: ddc.set_brightness(lambda current, _: current + step), "Brightness")
    if args.set is not None:
        return change(lambda: ddc.set_brightness(ddc.level(args.set)), "Brightness")
    if args.preset:
        from service import apply_preset
        return change(lambda: apply_preset(args.preset), args.preset)
    if args.apply_schedule:
        import service
        service.apply_schedule()
        return 0
    if args.background:
        import service
        return service.run()
    if args.setup or args.remove_setup:
        import integration
        integration.setup() if args.setup else integration.remove()
        return 0

    import gui
    return gui.run()


if __name__ == "__main__":
    sys.exit(main())
