"""Dimly's background service, running while you're logged in.

- Monitors follow the laptop screen, e.g. its brightness keys.
- Newly connected monitors are brought to the current level.
- The top-bar menu controls monitors through the D-Bus interface below. Its
  writes go through one ddc.Writer per monitor, so dragging a slider never
  piles up commands.
"""

import threading
import time
from datetime import datetime

from gi.repository import Gio, GLib

import ddc
import laptop

BUS_NAME = "io.github.saikatnjm.Dimly.Service"
OBJECT_PATH = "/io/github/saikatnjm/Dimly/Service"
INTERFACE = "io.github.saikatnjm.Dimly.Service"
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
# How long a laptop brightness change we made ourselves counts as our own echo.
ECHO_WINDOW = 3.0


def current_level():
    """Return the brightness level a monitor should have right now, or None if nothing says."""
    config = ddc.load_config()
    if config["sync_laptop"]:
        percent = laptop.get_brightness()
        if percent is not None:
            return percent
    slot = ddc.current_slot(config["schedule"], datetime.now()) if config["schedule_enabled"] else None
    return ddc.presets()[slot[1]] if slot else None


def add_new_monitors():
    """Search for monitors, and bring any new one to the current level."""
    known = set(ddc.load_state().get("buses") or [])
    displays = ddc.detect_displays()
    new = [bus for bus, _ in displays if bus not in known]
    if new:
        target = current_level()
        names = ", ".join(name for bus, name in displays if bus in new)
        print(f"New monitor: {names} (bus {', '.join(map(str, new))}), setting level {target}", flush=True)
        if target is not None:
            ddc.set_brightness_on(new, ddc.level(target))


def in_background(function, *args):
    # Monitor commands take up to a second; keep the main loop free for D-Bus calls.
    threading.Thread(target=function, args=args, daemon=True).start()


class Service:
    def __init__(self):
        self.connection = laptop.session_bus()
        self.writers = {}
        self.maxima = {}
        self.own_laptop_changes = []  # (level, time) we set on the laptop screen
        self.follow_timer = 0
        self.look_timers = {}
        self.last_emitted = None

        laptop.watch(self.laptop_changed)
        self.connection.signal_subscribe("org.gnome.Mutter.DisplayConfig", "org.gnome.Mutter.DisplayConfig",
                                         "MonitorsChanged", "/org/gnome/Mutter/DisplayConfig", None,
                                         Gio.DBusSignalFlags.NONE, self.monitors_changed)

        interface = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML).interfaces[0]
        self.connection.register_object(OBJECT_PATH, interface, self.method_call, None, None)
        Gio.bus_own_name_on_connection(self.connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None)

        ddc.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.state_monitor = Gio.File.new_for_path(str(ddc.STATE_FILE)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.state_monitor.connect("changed", lambda *_: self.emit_changed())
        in_background(self.read_levels)

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
        """Return (brightness, contrast) of the first monitor, -1 where unknown."""
        state = ddc.load_state()

        def first(key):
            recorded = state.get(key, {})
            return next((recorded[str(bus)] for bus in self.buses() if str(bus) in recorded), -1)

        return first("brightness"), first("contrast")

    def emit_changed(self):
        current = self.state()
        if current != self.last_emitted:
            self.last_emitted = current
            self.connection.emit_signal(None, OBJECT_PATH, INTERFACE, "Changed", GLib.Variant("(ii)", current))

    # Monitors

    def buses(self):
        return ddc.load_state().get("buses") or []

    def writer(self, bus):
        if bus not in self.writers:
            self.writers[bus] = ddc.Writer(bus)
        return self.writers[bus]

    def read_levels(self):
        """Record where the monitors are now, so the top-bar menu starts at the right place."""
        for bus in self.buses():
            for code in ddc.FEATURE_NAMES:
                reading = ddc.read_vcp(bus, code)
                if reading:
                    ddc.record_levels(code, {bus: reading[0]})
                    if code == ddc.BRIGHTNESS:
                        self.maxima[bus] = reading[1]

    def set_brightness(self, level):
        level = max(0, min(100, level))
        for bus in self.buses():
            self.writer(bus).request(ddc.BRIGHTNESS, round(level * self.maxima.get(bus, 100) / 100))
        if ddc.load_config()["sync_laptop"]:
            target = max(laptop.MINIMUM, level)
            self.own_laptop_changes.append((target, time.monotonic()))
            laptop.set_brightness(target, wait=False)

    def set_contrast(self, value):
        for bus in self.buses():
            self.writer(bus).request(ddc.CONTRAST, max(0, min(100, value)))

    # The laptop screen and hot-plugging

    def laptop_changed(self, percent):
        now = time.monotonic()
        self.own_laptop_changes = [(v, t) for v, t in self.own_laptop_changes if now - t < ECHO_WINDOW]
        for change in self.own_laptop_changes:
            if change[0] == percent:  # our own change coming back: the monitors already have it
                self.own_laptop_changes.remove(change)
                return
        # Wait until the keys stop: each monitor write takes about half a second.
        if self.follow_timer:
            GLib.source_remove(self.follow_timer)
        self.follow_timer = GLib.timeout_add(400, self.follow_laptop)

    def follow_laptop(self):
        self.follow_timer = 0
        percent = laptop.get_brightness()
        if percent is not None and ddc.load_config()["sync_laptop"]:
            in_background(ddc.set_brightness, ddc.level(percent), False)
        return GLib.SOURCE_REMOVE

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
    Service()
    GLib.MainLoop().run()
    return 0
