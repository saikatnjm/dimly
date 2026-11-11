"""Dimly's background service, running while you're logged in.

- Monitors follow the laptop screen, e.g. its brightness keys.
- The schedule switches presets at its set times. The service sleeps until the
  next switch instead of checking every few minutes.
- Newly connected monitors are brought to the current level.
- The top-bar menu controls monitors through the D-Bus interface below. Its
  writes go through one ddc.Writer per monitor, so dragging a slider never
  piles up commands.
"""

import subprocess
import threading
from datetime import datetime

from gi.repository import Gio, GLib

import ddc
import integration
import laptop
from notify import notify

BUS_NAME = "io.github.saikatnjm.Dimly.Service"
OBJECT_PATH = "/io/github/saikatnjm/Dimly/Service"
INTERFACE = "io.github.saikatnjm.Dimly.Service"
# Levels on this interface are percentages, whatever range a monitor uses.
INTERFACE_XML = f"""<node><interface name="{INTERFACE}">
  <method name="GetState">
    <arg name="brightness" type="i" direction="out"/>
    <arg name="contrast" type="i" direction="out"/>
  </method>
  <method name="SetBrightness"><arg name="level" type="i" direction="in"/></method>
  <method name="SetContrast"><arg name="value" type="i" direction="in"/></method>
  <method name="ApplyPreset"><arg name="name" type="s" direction="in"/></method>
  <signal name="Changed">
    <arg name="brightness" type="i"/>
    <arg name="contrast" type="i"/>
  </signal>
</interface></node>"""
# The longest the schedule sleeps between checks, so a clock change is caught
# within this time even if no switch is due.
SCHEDULE_MAX_SLEEP = 30 * 60
# Versions before 0.3.0 checked the schedule with this timer every 5 minutes.
OLD_SCHEDULE_TIMER = "dimly-schedule.timer"


def current_level():
    """Return the brightness level a monitor should have right now, or None if nothing says."""
    config = ddc.load_config()
    if config["sync_laptop"]:
        percent = laptop.get_brightness()
        if percent is not None:
            return percent
    slot = ddc.current_slot(config["schedule"], datetime.now()) if config["schedule_enabled"] else None
    return ddc.presets()[slot[1]] if slot else None


def read_levels(buses):
    """Record where these monitors are now, and their ranges, so menus start at the right place."""
    for bus in buses:
        for code in ddc.FEATURE_NAMES:
            reading = ddc.read_vcp(bus, code)
            if reading:
                ddc.record_levels(code, {bus: reading[0]}, {bus: reading[1]})


def add_new_monitors():
    """Search for monitors, and bring any new one to the current level."""
    known = set(ddc.load_state().get("buses") or [])
    displays = ddc.detect_displays()
    new = [bus for bus, _ in displays if bus not in known]
    if new:
        target = current_level()
        names = ", ".join(name for bus, name in displays if bus in new)
        print(f"New monitor: {names} (bus {', '.join(map(str, new))}), setting level {target}", flush=True)
        read_levels(new)
        if target is not None:
            ddc.set_brightness_on(new, ddc.level(target))


def apply_schedule():
    """Apply the scheduled preset once per time slot, so a change made by hand lasts until the next slot."""
    config = ddc.load_config()
    slot = ddc.current_slot(config["schedule"], datetime.now()) if config["schedule_enabled"] else None
    if slot is None or ddc.load_state().get("applied_slot") == slot[0]:
        return
    key, preset = slot
    # When no monitor answers, leave the slot unapplied so the next check tries again.
    if ddc.set_brightness(ddc.level(ddc.presets()[preset])):
        ddc.update_state(applied_slot=key)
        notify(f"{preset} brightness", f"Switched by your schedule at {key.split()[1]}")


def in_background(function, *args):
    # Monitor commands take up to a second; keep the main loop free for D-Bus calls.
    threading.Thread(target=function, args=args, daemon=True).start()


class Service:
    def __init__(self, loop):
        self.loop = loop
        self.connection = laptop.session_bus()
        self.system_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)  # held: GLib drops unreferenced connections
        self.writers = {}
        self.follow_timer = 0
        self.look_timers = {}
        self.schedule_timer = 0
        self.config_timer = 0
        self.last_emitted = None

        laptop.watch(self.laptop_changed)
        self.connection.signal_subscribe("org.gnome.Mutter.DisplayConfig", "org.gnome.Mutter.DisplayConfig",
                                         "MonitorsChanged", "/org/gnome/Mutter/DisplayConfig", None,
                                         Gio.DBusSignalFlags.NONE, self.monitors_changed)
        # Timers stand still while the computer sleeps, so check the schedule on waking up.
        self.system_bus.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager",
                                         "PrepareForSleep", "/org/freedesktop/login1", None,
                                         Gio.DBusSignalFlags.NONE, self.sleep_changed)

        interface = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML).interfaces[0]
        self.connection.register_object(OBJECT_PATH, interface, self.method_call, None, None)
        Gio.bus_own_name_on_connection(self.connection, BUS_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
                                       None, self.name_lost)

        ddc.STATE_DIR.mkdir(parents=True, exist_ok=True)
        ddc.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.state_monitor = Gio.File.new_for_path(str(ddc.STATE_FILE)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.state_monitor.connect("changed", lambda *_: self.emit_changed())
        self.config_monitor = Gio.File.new_for_path(str(ddc.CONFIG_FILE)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.config_monitor.connect("changed", lambda *_: self.config_changed())
        in_background(self.start_up)

    def name_lost(self, *_):
        print(f"Another Dimly service already owns {BUS_NAME}; stopping this one.", flush=True)
        self.loop.quit()

    def start_up(self):
        subprocess.run(["systemctl", "--user", "disable", "--now", OLD_SCHEDULE_TIMER], capture_output=True)
        # After an Ubuntu upgrade, GNOME may need the other version of the top-bar menu.
        if integration.top_bar_enabled() and integration.link_extension():
            print(f"Linked the top-bar menu for GNOME {integration.gnome_version()}; "
                  "it appears after the next log in.", flush=True)
        # Find the monitors first, so the top-bar menu works straight after a fresh install.
        read_levels([bus for bus, _ in ddc.detect_displays()])
        apply_schedule()
        GLib.idle_add(self.schedule_next)

    # D-Bus interface

    def method_call(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        args = parameters.unpack()
        if method == "GetState":
            invocation.return_value(GLib.Variant("(ii)", self.state()))
            return
        if method == "SetBrightness":
            self.set_brightness(args[0])
        elif method == "SetContrast":
            self.set_contrast(args[0])
        elif method == "ApplyPreset":
            presets = ddc.presets()
            if args[0] not in presets:
                invocation.return_dbus_error(f"{INTERFACE}.Error.UnknownPreset", f"No preset called {args[0]}")
                return
            self.set_brightness(presets[args[0]])
        invocation.return_value(None)

    def state(self):
        """Return (brightness, contrast) of the first monitor in percent, -1 where unknown."""
        state = ddc.load_state()
        buses = state.get("buses") or []

        def first(code):
            recorded = state.get(ddc.FEATURE_NAMES[code], {})
            for bus in buses:
                if str(bus) in recorded:
                    return ddc.to_percent(recorded[str(bus)], ddc.recorded_maximum(state, bus, code))
            return -1

        return first(ddc.BRIGHTNESS), first(ddc.CONTRAST)

    def emit_changed(self):
        current = self.state()
        if current != self.last_emitted:
            self.last_emitted = current
            self.connection.emit_signal(None, OBJECT_PATH, INTERFACE, "Changed", GLib.Variant("(ii)", current))

    # Monitors

    def writer(self, bus):
        if bus not in self.writers:
            self.writers[bus] = ddc.Writer(bus)
        return self.writers[bus]

    def request_all(self, code, percent):
        state = ddc.load_state()
        for bus in state.get("buses") or []:
            maximum = ddc.recorded_maximum(state, bus, code)
            self.writer(bus).request(code, ddc.from_percent(max(0, min(100, percent)), maximum))

    def set_brightness(self, percent):
        self.request_all(ddc.BRIGHTNESS, percent)
        if ddc.load_config()["sync_laptop"]:
            laptop.set_brightness(percent, wait=False)

    def set_contrast(self, percent):
        self.request_all(ddc.CONTRAST, percent)

    # The laptop screen

    def laptop_changed(self, percent):
        if laptop.is_own_change(percent):
            return  # Dimly set this itself, and the monitors already have it
        # Wait until the keys stop: each monitor write takes about half a second.
        if self.follow_timer:
            GLib.source_remove(self.follow_timer)
        self.follow_timer = GLib.timeout_add(400, self.follow_laptop)

    def follow_laptop(self):
        self.follow_timer = 0
        if ddc.load_config()["sync_laptop"]:
            in_background(self.follow_laptop_now)
        return GLib.SOURCE_REMOVE

    def follow_laptop_now(self):
        # The level is read inside the monitor lock, so if key presses queue up
        # several of these, each applies the newest level rather than an old one.
        def newest(current, maximum):
            percent = laptop.get_brightness()
            return current if percent is None else ddc.from_percent(percent, maximum)

        ddc.set_brightness(newest, sync_laptop=False)

    # The schedule

    def schedule_next(self):
        if self.schedule_timer:
            GLib.source_remove(self.schedule_timer)
            self.schedule_timer = 0
        config = ddc.load_config()
        wait = ddc.seconds_until_next_switch(config["schedule"], datetime.now()) if config["schedule_enabled"] else None
        if wait is not None:
            self.schedule_timer = GLib.timeout_add_seconds(min(wait + 1, SCHEDULE_MAX_SLEEP), self.schedule_due)
        return GLib.SOURCE_REMOVE

    def schedule_due(self):
        self.schedule_timer = 0
        in_background(apply_schedule)
        self.schedule_next()
        return GLib.SOURCE_REMOVE

    def config_changed(self):
        # Typing a time saves on every key, so wait for a pause.
        if self.config_timer:
            GLib.source_remove(self.config_timer)
        self.config_timer = GLib.timeout_add(1000, self.config_settled)

    def config_settled(self):
        self.config_timer = 0
        in_background(apply_schedule)
        self.schedule_next()
        return GLib.SOURCE_REMOVE

    def sleep_changed(self, _connection, _sender, _path, _interface, _signal, parameters):
        (going_to_sleep,) = parameters.unpack()
        if not going_to_sleep:
            in_background(apply_schedule)
            self.schedule_next()

    # Hot-plugging

    def monitors_changed(self, *_):
        # A just-connected monitor answers DDC/CI only after a few seconds, so look
        # twice. Unplugging also lands here; the cached list then just gets shorter.
        for timer in self.look_timers.values():
            GLib.source_remove(timer)
        self.look_timers = {seconds: GLib.timeout_add_seconds(seconds, self.look, seconds) for seconds in (4, 15)}

    def look(self, seconds):
        del self.look_timers[seconds]
        in_background(add_new_monitors)
        return GLib.SOURCE_REMOVE


def run():
    loop = GLib.MainLoop()
    Service(loop)
    loop.run()
    return 0
