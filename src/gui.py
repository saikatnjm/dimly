"""The Dimly window: sliders and presets per monitor, laptop sync, the schedule, and the shortcuts."""

import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

import ddc  # noqa: E402
import integration  # noqa: E402
import laptop  # noqa: E402

class Writer(threading.Thread):
    """Sends the latest requested value per feature to one monitor, one write at a time."""

    def __init__(self, bus, on_error):
        super().__init__(daemon=True)
        self.bus = bus
        self.on_error = on_error
        self.pending = {}
        self.cond = threading.Condition()

    def request(self, code, value):
        with self.cond:
            self.pending[code] = value
            self.cond.notify()

    def run(self):
        while True:
            with self.cond:
                while not self.pending:
                    self.cond.wait()
                code, value = self.pending.popitem()
            for _ in range(3):
                if ddc.write_vcp(self.bus, code, value):
                    break
                with self.cond:
                    if code in self.pending:  # a newer value replaces this one
                        break
                time.sleep(0.3)
            else:
                GLib.idle_add(self.on_error, code, value)


class MonitorGroup(Adw.PreferencesGroup):
    def __init__(self, writer, name, values):
        super().__init__(title=name)
        self.writer = writer
        self.brightness = None
        if values.get(ddc.BRIGHTNESS):
            self.brightness = self._slider_row("Brightness", ddc.BRIGHTNESS, *values[ddc.BRIGHTNESS])
            self.add(self._preset_row())
        if values.get(ddc.CONTRAST):
            self._slider_row("Contrast", ddc.CONTRAST, *values[ddc.CONTRAST])
        self.add(self._reset_row(name))

    def _slider_row(self, title, code, current, maximum):
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, maximum, 1)
        scale.set_value(current)
        scale.set_round_digits(0)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        # Fixed width so every slider lines up whatever its title's length.
        scale.set_size_request(300, -1)
        scale.set_valign(Gtk.Align.CENTER)
        scale.connect("value-changed", self._changed, code, maximum)
        row = Adw.ActionRow(title=title)
        row.add_suffix(scale)
        self.add(row)
        return scale

    def _changed(self, scale, code, maximum):
        value = int(scale.get_value())
        self.writer.request(code, value)
        if code == ddc.BRIGHTNESS and ddc.load_config()["sync_laptop"]:
            laptop.set_brightness(round(100 * value / maximum), wait=False)

    def _preset_row(self):
        # A row of its own: as an ActionRow suffix the buttons squeeze the title.
        buttons = Gtk.Box(spacing=6, homogeneous=True, margin_top=10, margin_bottom=10,
                          margin_start=12, margin_end=12)
        for label, value in ddc.presets().items():
            button = Gtk.Button(label=label, tooltip_text=f"Brightness {value}")
            button.connect("clicked", lambda _, v=value: self.brightness.set_value(v))
            buttons.append(button)
        return Adw.PreferencesRow(activatable=False, child=buttons)

    def _reset_row(self, name):
        button = Gtk.Button(label="Reset…", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _: self.get_root().confirm_reset(self.writer.bus, name))
        row = Adw.ActionRow(title="Factory reset",
                            subtitle="Fixes brightness not dimming the screen. Also resets colours and sharpness.")
        row.add_suffix(button)
        return row


class ScheduleGroup(Adw.PreferencesGroup):
    """Edits the schedule in the config file; the timer reads it from there."""

    def __init__(self):
        super().__init__(title="Schedule",
                         description="Switches preset at these times. A change you make yourself "
                                     "lasts until the next one.")
        self.config = ddc.load_config()
        self.presets = ddc.PRESET_NAMES

        switch = Gtk.Switch(active=self.config["schedule_enabled"], valign=Gtk.Align.CENTER)
        switch.connect("notify::active", self._toggled)
        row = Adw.ActionRow(title="Change brightness by time of day", activatable_widget=switch)
        row.add_suffix(switch)
        self.add(row)

        self.slot_rows = []
        for i, (at, preset) in enumerate(self.config["schedule"]):
            entry = Gtk.Entry(text=at, width_chars=5, max_width_chars=5, xalign=0.5,
                              valign=Gtk.Align.CENTER, tooltip_text="Time, as HH:MM (24-hour)")
            entry.connect("changed", self._time_changed, i)
            dropdown = Gtk.DropDown.new_from_strings(self.presets)
            dropdown.set_selected(self.presets.index(preset) if preset in self.presets else 0)
            dropdown.set_valign(Gtk.Align.CENTER)
            dropdown.connect("notify::selected", self._preset_changed, i)
            row = Adw.ActionRow(title="switch to", sensitive=self.config["schedule_enabled"])
            row.add_prefix(entry)
            row.add_suffix(dropdown)
            self.add(row)
            self.slot_rows.append(row)

    def _toggled(self, switch, _):
        for row in self.slot_rows:
            row.set_sensitive(switch.get_active())
        ddc.update_config(schedule_enabled=switch.get_active())

    def _time_changed(self, entry, i):
        at = ddc.parse_time(entry.get_text())
        if at is None:
            entry.add_css_class("error")
            return
        entry.remove_css_class("error")
        self.config["schedule"][i][0] = at
        ddc.update_config(schedule=self.config["schedule"])

    def _preset_changed(self, dropdown, _, i):
        self.config["schedule"][i][1] = self.presets[dropdown.get_selected()]
        ddc.update_config(schedule=self.config["schedule"])


class LaptopGroup(Adw.PreferencesGroup):
    def __init__(self):
        super().__init__(title="Laptop screen")
        switch = Gtk.Switch(active=ddc.load_config()["sync_laptop"], valign=Gtk.Align.CENTER)
        switch.connect("notify::active", self._toggled)
        row = Adw.ActionRow(title="Keep in sync with the monitor", activatable_widget=switch,
                            subtitle="Same brightness on both; the laptop's brightness keys change both too")
        row.add_suffix(switch)
        self.add(row)

    def _toggled(self, switch, _):
        ddc.update_config(sync_laptop=switch.get_active())


class ShortcutsGroup(Adw.PreferencesGroup):
    def __init__(self):
        super().__init__(title="Keyboard shortcuts",
                         description=f"Change brightness in steps of {ddc.load_config()['step']}. "
                                     "To change the keys, go to Settings → Keyboard → Custom Shortcuts.")
        for shortcut_id, name, *_ in integration.SHORTCUTS:
            row = Adw.ActionRow(title=name.removeprefix("Dimly: ").capitalize())
            accel = integration.shortcut_binding(shortcut_id)
            if accel:
                row.add_suffix(Gtk.ShortcutLabel(accelerator=accel, valign=Gtk.Align.CENTER))
            else:
                row.set_subtitle("No keys set")
            self.add(row)


class DimlyWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Dimly", default_width=520, default_height=640)
        self.writers = {}
        self.has_laptop_screen = False

        header = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Find monitors again")
        refresh.connect("clicked", lambda _: self.load())
        header.pack_start(refresh)
        menu = Gio.Menu()
        menu.append("About Dimly", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu"))

        self.content = Adw.Bin(vexpand=True)
        self.toasts = Adw.ToastOverlay(child=self.content)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(self.toasts)
        self.set_content(box)
        self.load()

    def load(self):
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        self.content.set_child(Adw.StatusPage(title="Looking for monitors…", child=spinner))
        threading.Thread(target=self._find_monitors, daemon=True).start()

    def _find_monitors(self):
        problem = ddc.access_problem()
        if problem:
            GLib.idle_add(self._show, [], problem)
            return
        try:
            found = [(bus, name, {code: ddc.read_vcp(bus, code) for code in ddc.FEATURE_NAMES})
                     for bus, name in ddc.detect_displays()]
            self.has_laptop_screen = laptop.get_brightness() is not None
            GLib.idle_add(self._show, found, None)
        except (OSError, subprocess.SubprocessError) as e:
            GLib.idle_add(self._show, [], str(e))

    def _show(self, found, error):
        found = [m for m in found if any(m[2].values())]
        page = Adw.PreferencesPage()
        if not found:
            page.add(self._no_monitor_group(error))
        for bus, name, values in found:
            if bus not in self.writers:
                self.writers[bus] = Writer(bus, lambda code, value, n=name: self._write_failed(n, code, value))
                self.writers[bus].start()
            page.add(MonitorGroup(self.writers[bus], name, values))
        if self.has_laptop_screen:
            page.add(LaptopGroup())
        page.add(ScheduleGroup())
        page.add(ShortcutsGroup())
        self.content.set_child(page)

    def _no_monitor_group(self, error):
        group = Adw.PreferencesGroup()
        group.add(Adw.StatusPage(
            icon_name="video-display-symbolic",
            title="No adjustable monitor found",
            description=error or "Check the cable, and turn on DDC/CI in the monitor's own menu. "
                                 "Laptop screens aren't listed: use the brightness keys for those.",
        ))
        return group

    def confirm_reset(self, bus, name):
        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
            text=f"Reset {name} to factory settings?",
            secondary_text="Colours and sharpness go back to factory values. "
                           "Brightness and contrast stay where they are.",
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Reset", Gtk.ResponseType.ACCEPT).add_css_class("destructive-action")
        dialog.connect("response", self._reset_response, bus, name)
        dialog.present()

    def _reset_response(self, dialog, response, bus, name):
        dialog.destroy()
        if response != Gtk.ResponseType.ACCEPT:
            return
        self.content.set_child(Adw.StatusPage(title=f"Resetting {name}…",
                                              child=Gtk.Spinner(spinning=True, width_request=32, height_request=32)))

        def reset():
            ok = ddc.factory_reset(bus)
            GLib.idle_add(self._reset_done, name, ok)

        threading.Thread(target=reset, daemon=True).start()

    def _reset_done(self, name, ok):
        self.toasts.add_toast(Adw.Toast(title=f"{name} reset to factory settings" if ok
                                        else f"{name} didn't accept the reset. Try again."))
        self.load()

    def _write_failed(self, name, code, value):
        self.toasts.add_toast(Adw.Toast(title=f"{name} didn't accept {ddc.FEATURE_NAMES[code]} {value}. Try again."))


class DimlyApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=integration.APP_ID)

    def do_startup(self):
        Adw.Application.do_startup(self)
        for name, callback in [("quit", lambda *_: self.quit()), ("about", self._about)]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Ctrl>q"])

    def do_activate(self):
        if integration.needs_setup():
            integration.setup()
        (self.props.active_window or DimlyWindow(self)).present()

    def _about(self, *_):
        Gtk.AboutDialog(
            transient_for=self.props.active_window, modal=True,
            program_name="Dimly", version=integration.VERSION, logo_icon_name=integration.APP_ID,
            comments="Easy-on-the-eyes brightness for external monitors, in step with your laptop screen.",
            website=integration.WEBSITE, license_type=Gtk.License.MIT_X11, authors=["Saikat Roy"],
        ).present()


def run():
    return DimlyApp().run(None)
