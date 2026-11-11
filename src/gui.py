"""The Dimly window: sliders and presets per monitor, laptop sync, the schedule, and the shortcuts."""

import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

import about  # noqa: E402
import ddc  # noqa: E402
import integration  # noqa: E402
import laptop  # noqa: E402
import warmth  # noqa: E402

# After you move a slider, outside updates wait this long, so a change that's
# still on its way back from the laptop doesn't pull the slider from under you.
LOCAL_CHANGE_GRACE = 2.0


class MonitorGroup(Adw.PreferencesGroup):
    def __init__(self, writer, name, values):
        super().__init__(title=name)
        self.writer = writer
        self.scales = {}
        self.preset_buttons = {}
        self.handlers = {}
        self.last_local_change = 0.0
        self.catch_up = 0
        if values.get(ddc.BRIGHTNESS):
            self._slider_row("Brightness", ddc.BRIGHTNESS, *values[ddc.BRIGHTNESS])
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
        self.handlers[code] = scale.connect("value-changed", self._changed, code, maximum)
        self.scales[code] = scale
        row = Adw.ActionRow(title=title)
        row.add_suffix(scale)
        self.add(row)

    def _changed(self, scale, code, maximum):
        self.last_local_change = time.monotonic()
        value = int(scale.get_value())
        self.writer.request(code, value)
        if code == ddc.BRIGHTNESS and ddc.load_config()["sync_laptop"]:
            laptop.set_brightness(round(100 * value / maximum), wait=False)

    def show_recorded(self, state):
        """Move the sliders to levels set outside the window, without sending them back out."""
        wait = LOCAL_CHANGE_GRACE - (time.monotonic() - self.last_local_change)
        if wait > 0:
            # Catch up once you've let go, with whatever was recorded last.
            if not self.catch_up:
                self.catch_up = GLib.timeout_add(int(wait * 1000) + 50, self._catch_up)
            return
        for code, scale in self.scales.items():
            value = state.get(ddc.FEATURE_NAMES[code], {}).get(str(self.writer.bus))
            if value is not None:
                with scale.handler_block(self.handlers[code]):
                    scale.set_value(value)

    def _catch_up(self):
        self.catch_up = 0
        self.show_recorded(ddc.load_state())
        return GLib.SOURCE_REMOVE

    def _preset_row(self):
        # A row of its own: as an ActionRow suffix the buttons squeeze the title.
        buttons = Gtk.Box(spacing=6, homogeneous=True, margin_top=10, margin_bottom=10,
                          margin_start=12, margin_end=12)
        for name in ddc.PRESET_NAMES:
            button = Gtk.Button(label=name)
            # Read the level on click, so edits in Preset levels apply straight away.
            button.connect("clicked", lambda _, n=name: self._apply_preset(n))
            buttons.append(button)
            self.preset_buttons[name] = button
        self.update_preset_tooltips()
        return Adw.PreferencesRow(activatable=False, child=buttons)

    def _apply_preset(self, name):
        # Presets are percentages; the slider runs over the monitor's own range.
        scale = self.scales[ddc.BRIGHTNESS]
        scale.set_value(ddc.from_percent(ddc.presets()[name], scale.get_adjustment().get_upper()))

    def update_preset_tooltips(self):
        for name, level in ddc.presets().items():
            if name in self.preset_buttons:
                self.preset_buttons[name].set_tooltip_text(f"Brightness {level}")

    def _reset_row(self, name):
        button = Gtk.Button(label="Reset…", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _: self.get_root().confirm_reset(self.writer.bus, name))
        row = Adw.ActionRow(title="Factory reset",
                            subtitle="Fixes brightness not dimming the screen. Also resets colours and sharpness.")
        row.add_suffix(button)
        return row


class PresetsGroup(Adw.PreferencesGroup):
    """Edits what each preset means. The buttons, schedule and shortcuts read it from the config file."""

    def __init__(self, on_change):
        super().__init__(title="Preset levels",
                         description="The brightness each preset sets, from 0 to 100. "
                                     "The buttons and the schedule use these.")
        self.on_change = on_change
        for name, level in ddc.presets().items():
            spin = Gtk.SpinButton.new_with_range(0, 100, 5)
            spin.set_value(level)
            spin.set_numeric(True)
            spin.set_valign(Gtk.Align.CENTER)
            spin.connect("value-changed", self._changed, name)
            row = Adw.ActionRow(title=name)
            row.add_suffix(spin)
            self.add(row)

    def _changed(self, spin, name):
        ddc.update_config(presets={**ddc.presets(), name: spin.get_value_as_int()})
        self.on_change()


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


class WarmthGroup(Adw.PreferencesGroup):
    """The desktop's night light, for every screen. The desktop keeps the setting,
    so this also follows changes made in its own Settings."""

    WHEN_LABELS = ["Night Light's own hours", "All the time"]  # for warmth.WHEN, in order

    def __init__(self, night_light):
        super().__init__(title="Warmth", description="Less blue light, on every screen. 0 turns it off.")
        self.night_light = night_light
        self.preview_timer = 0
        self.catch_up = 0
        self.last_local_change = 0.0
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.scale.set_round_digits(0)
        self.scale.set_draw_value(True)
        self.scale.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale.set_size_request(300, -1)
        self.scale.set_valign(Gtk.Align.CENTER)
        self.scale_handler = self.scale.connect("value-changed", self._changed)
        row = Adw.ActionRow(title="Warmth")
        row.add_suffix(self.scale)
        self.add(row)
        self.when = Adw.ComboRow(title="When", model=Gtk.StringList.new(self.WHEN_LABELS))
        self.when_handler = self.when.connect("notify::selected", self._when_changed)
        self.add(self.when)
        self.show_settings()

    def show_settings(self):
        wait = LOCAL_CHANGE_GRACE - (time.monotonic() - self.last_local_change)
        if wait > 0:
            # Probably your own drag coming back; look again once you've let go.
            if not self.catch_up:
                self.catch_up = GLib.timeout_add(int(wait * 1000) + 50, self._catch_up)
            return
        level = warmth.get_warmth(self.night_light)
        with self.scale.handler_block(self.scale_handler):
            self.scale.set_value(level)
        with self.when.handler_block(self.when_handler):
            self.when.set_selected(warmth.WHEN.index(warmth.get_when(self.night_light)))
        self.when.set_sensitive(level > 0)

    def _catch_up(self):
        self.catch_up = 0
        self.show_settings()
        return GLib.SOURCE_REMOVE

    def _changed(self, scale):
        self.last_local_change = time.monotonic()
        level = int(scale.get_value())
        warmth.set_warmth(self.night_light, level)
        self.when.set_sensitive(level > 0)
        # Show the warmth even if the night light isn't due yet, once you pause.
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)
        self.preview_timer = GLib.timeout_add(300, self._preview) if level > 0 else 0

    def _preview(self):
        self.preview_timer = 0
        warmth.preview()
        return GLib.SOURCE_REMOVE

    def _when_changed(self, row, _):
        warmth.set_when(self.night_light, warmth.WHEN[row.get_selected()])


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


class TopBarGroup(Adw.PreferencesGroup):

    def __init__(self):
        bar = integration.panel_name()
        super().__init__(title=bar.capitalize())
        switch = Gtk.Switch(active=integration.top_bar_enabled(), valign=Gtk.Align.CENTER)
        switch.connect("notify::active", self._toggled)
        self.row = Adw.ActionRow(title=f"Show Dimly in the {bar}", activatable_widget=switch)
        self.row.add_suffix(switch)
        self.add(self.row)

        # Labels for ddc.TOP_BAR_POSITIONS, in the same order. GNOME's clock is in the middle.
        labels = ["Right", "Centre" if integration.on_cinnamon() else "Next to the clock", "Left"]
        self.position = Adw.ComboRow(title="Position", model=Gtk.StringList.new(labels))
        self.position.set_selected(ddc.TOP_BAR_POSITIONS.index(ddc.load_config()["top_bar_position"]))
        self.position.connect("notify::selected", self._moved)
        self.add(self.position)
        self._update(switch.get_active())

    def _toggled(self, switch, _):
        integration.set_top_bar(switch.get_active())
        self._update(switch.get_active())
        # GNOME applies the switch a moment later; show its answer once it has.
        GLib.timeout_add(1500, lambda: self._update(switch.get_active()) or GLib.SOURCE_REMOVE)

    def _moved(self, row, _):
        # The menu moves straight away.
        ddc.update_config(top_bar_position=ddc.TOP_BAR_POSITIONS[row.get_selected()])
        integration.apply_position()

    def _update(self, enabled):
        self.row.set_subtitle(integration.top_bar_status())
        self.position.set_sensitive(enabled)


class ShortcutsGroup(Adw.PreferencesGroup):
    def __init__(self):
        super().__init__(title="Keyboard shortcuts",
                         # No "→" here: GNOME 42 lays out long descriptions with arrows too wide.
                         description=f"Each press changes brightness by {ddc.load_config()['step']}. "
                                     "You can change the keys in GNOME Settings, under Keyboard.")
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

        self.monitor_groups = {}
        ddc.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state_monitor = Gio.File.new_for_path(str(ddc.STATE_FILE)).monitor_file(Gio.FileMonitorFlags.NONE, None)
        self.state_monitor.connect("changed", self._state_changed)
        self.warmth_group = None
        self.night_light = warmth.settings()
        if self.night_light:
            self.night_light.connect("changed", lambda *_: self.warmth_group and self.warmth_group.show_settings())
        self.load()

    def _state_changed(self, _monitor, _file, _other, event):
        if event == Gio.FileMonitorEvent.DELETED:
            return
        state = ddc.load_state()
        for group in self.monitor_groups.values():
            group.show_recorded(state)

    def _presets_changed(self):
        for group in self.monitor_groups.values():
            group.update_preset_tooltips()

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
            # Share what was read, so the top-bar menu shows the same levels.
            for bus, _, values in found:
                for code, reading in values.items():
                    if reading:
                        ddc.record_levels(code, {bus: reading[0]}, {bus: reading[1]})
            self.has_laptop_screen = laptop.get_brightness() is not None
            GLib.idle_add(self._show, found, None)
        except (OSError, subprocess.SubprocessError) as e:
            GLib.idle_add(self._show, [], str(e))

    def _show(self, found, error):
        found = [m for m in found if any(m[2].values())]
        page = Adw.PreferencesPage()
        if not found:
            page.add(self._no_monitor_group(error))
        self.monitor_groups = {}
        for bus, name, values in found:
            if bus not in self.writers:
                self.writers[bus] = ddc.Writer(
                    bus, lambda code, value, n=name: GLib.idle_add(self._write_failed, n, code, value))
            self.monitor_groups[bus] = MonitorGroup(self.writers[bus], name, values)
            page.add(self.monitor_groups[bus])
        if self.night_light:
            self.warmth_group = WarmthGroup(self.night_light)
            page.add(self.warmth_group)
        if self.has_laptop_screen:
            page.add(LaptopGroup())
        page.add(PresetsGroup(self._presets_changed))
        page.add(ScheduleGroup())
        if integration.top_bar_available():
            page.add(TopBarGroup())
        page.add(ShortcutsGroup())
        page.add(self._footer())
        self.content.set_child(page)

    def _footer(self):
        # Small and grey on purpose: findable when you want updates, easy to ignore otherwise.
        label = Gtk.Label(use_markup=True, margin_top=6, css_classes=["caption", "dim-label"])
        label.set_markup(f'Dimly {about.VERSION} · <a href="{about.SOURCE}/releases/latest">'
                         'Latest version on GitHub</a>')
        label.set_tooltip_text(f"{about.SOURCE}/releases/latest")
        group = Adw.PreferencesGroup()
        group.add(label)
        return group

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
        super().__init__(application_id=about.APP_ID)

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
            program_name="Dimly", version=about.VERSION, logo_icon_name=about.APP_ID,
            comments="Easy-on-the-eyes brightness for external monitors, in step with your laptop screen.",
            website=about.WEBSITE, license_type=Gtk.License.MIT_X11, authors=["Saikat Roy"],
        ).present()


def run():
    return DimlyApp().run(None)
