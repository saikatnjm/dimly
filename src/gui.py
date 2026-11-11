"""The Dimly window: sliders and presets per monitor, warmth, laptop sync, the schedule, and the shortcuts."""

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
        if values.get(ddc.RED_GAIN) and values.get(ddc.BLUE_GAIN):
            self.add(self._colour_row())
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
        config = ddc.load_config()
        if code == ddc.BRIGHTNESS and config["sync_laptop"]:
            laptop.set_brightness(ddc.laptop_level(round(100 * value / maximum)), wait=False)
        if config["sync_monitors"]:
            self.get_root().sync_monitors(self, code, ddc.to_percent(value, maximum))

    def follow(self, code, percent):
        """Take another monitor's level, in percent, on this monitor's own range."""
        scale = self.scales.get(code)
        if scale is None:
            return
        value = ddc.from_percent(percent, int(scale.get_adjustment().get_upper()))
        self.last_local_change = time.monotonic()
        with scale.handler_block(self.handlers[code]):  # so it doesn't pass it on again
            scale.set_value(value)
        self.writer.request(code, value)

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
            # Read the preset on click, so edits in Presets apply straight away.
            button.connect("clicked", lambda _, n=name: self._apply_preset(n))
            buttons.append(button)
            self.preset_buttons[name] = button
        self.update_preset_tooltips()
        return Adw.PreferencesRow(activatable=False, child=buttons)

    def _apply_preset(self, name):
        # Moving the sliders sends the levels. Presets are percentages; each
        # slider runs over the monitor's own range.
        preset = ddc.presets()[name]
        for code, key in ((ddc.BRIGHTNESS, "brightness"), (ddc.CONTRAST, "contrast")):
            scale = self.scales.get(code)
            if scale and preset[key] is not None:
                scale.set_value(ddc.from_percent(preset[key], scale.get_adjustment().get_upper()))
        if preset["warmth"] is not None:
            self.get_root().set_warmth(preset["warmth"])

    def update_preset_tooltips(self):
        for name, preset in ddc.presets().items():
            if name in self.preset_buttons:
                self.preset_buttons[name].set_tooltip_text(ddc.describe(preset))

    def _colour_row(self):
        # For matching monitors of different makes. The base is read, and the
        # monitor put in its user colour mode, only once the slider is first used.
        self.colour_base = ddc.saved_colour_base(self.writer.bus)
        self.colour_balance = self.colour_base["balance"] if self.colour_base else 0
        self.colour_loading = False
        self.colour_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,
                                                     -ddc.COLOUR_RANGE, ddc.COLOUR_RANGE, 1)
        self.colour_scale.set_value(self.colour_balance if type(self.colour_balance) is int else 0)
        self.colour_scale.set_round_digits(0)
        self.colour_scale.set_has_origin(False)  # no fill: it runs both ways from the middle
        self.colour_scale.add_mark(0, Gtk.PositionType.BOTTOM, None)
        self.colour_scale.set_size_request(260, -1)
        self.colour_scale.set_valign(Gtk.Align.CENTER)
        self.colour_handler = self.colour_scale.connect("value-changed", self._colour_changed)
        reset = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                           tooltip_text="Back to the monitor's own colour")
        reset.connect("clicked", self._colour_reset)
        row = Adw.ActionRow(title="Colour", subtitle="Cooler or warmer")
        row.add_suffix(self.colour_scale)
        row.add_suffix(reset)
        return row

    def _colour_changed(self, scale):
        self.colour_balance = int(scale.get_value())
        if self.colour_base:
            self._send_colour()
        elif not self.colour_loading:
            self.colour_loading = True
            bus = self.writer.bus
            threading.Thread(target=lambda: GLib.idle_add(self._colour_loaded, ddc.colour_base(bus)),
                             daemon=True).start()

    def _colour_loaded(self, base):
        self.colour_loading = False
        self.colour_base = base
        if base:
            self._send_colour()  # whatever the slider shows by now
        return GLib.SOURCE_REMOVE

    def _send_colour(self):
        red, blue = ddc.colour_gains(self.colour_base, self.colour_balance)
        self.writer.request(ddc.RED_GAIN, red)
        self.writer.request(ddc.BLUE_GAIN, blue)
        ddc.save_colour_balance(self.writer.bus, self.colour_balance)

    def _colour_reset(self, _button):
        with self.colour_scale.handler_block(self.colour_handler):
            self.colour_scale.set_value(0)
        self.colour_balance = 0
        if self.colour_base:
            self._send_colour()  # the base itself

    def _reset_row(self, name):
        button = Gtk.Button(label="Reset…", valign=Gtk.Align.CENTER)
        button.connect("clicked", lambda _: self.get_root().confirm_reset(self.writer.bus, name))
        row = Adw.ActionRow(title="Factory reset",
                            subtitle="Fixes brightness not dimming the screen. Keeps your levels and colour.")
        row.add_suffix(button)
        return row


class PresetsGroup(Adw.PreferencesGroup):
    """Edits what each preset sets. The buttons, schedule and shortcuts read it from the config file."""

    def __init__(self, on_change, with_warmth):
        super().__init__(title="Presets",
                         description="What each preset sets, from 0 to 100. "
                                     "The buttons, the menu and the schedule use these.")
        self.on_change = on_change
        settings = ddc.PRESET_SETTINGS if with_warmth else ddc.PRESET_SETTINGS[:2]
        for name, preset in ddc.presets().items():
            row = Adw.ExpanderRow(title=name, subtitle=ddc.describe(preset))
            for key in settings:
                # Below 0 means "Keep": the preset leaves this setting as it is.
                lowest = 0 if key == "brightness" else -5
                spin = Gtk.SpinButton.new_with_range(lowest, 100, 5)
                spin.set_numeric(False)  # so it can show "Keep"
                spin.connect("output", self._show_keep)
                spin.set_value(lowest if preset[key] is None else preset[key])
                spin.set_valign(Gtk.Align.CENTER)
                spin.connect("value-changed", self._changed, name, key, row)
                setting = Adw.ActionRow(title=key.capitalize())
                if key != "brightness":
                    setting.set_subtitle("Below 0 keeps it as it is")
                setting.add_suffix(spin)
                row.add_row(setting)
            self.add(row)

    @staticmethod
    def _show_keep(spin):
        if spin.get_value() >= 0:
            return False
        spin.set_text("Keep")
        return True

    def _changed(self, spin, name, key, row):
        value = spin.get_value_as_int()
        presets = ddc.presets()
        presets[name][key] = value if value >= 0 else None
        ddc.update_config(presets=presets)
        row.set_subtitle(ddc.describe(presets[name]))
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
        row = Adw.ActionRow(title="Switch presets by time of day", activatable_widget=switch)
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
    """Warmth for every screen: the desktop's night light where there is one, so
    this also follows changes made in its own Settings; otherwise Dimly's own tint."""

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
        self.when = None
        if night_light:  # Dimly's own tint is simply on or off
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
        level = warmth.get_level() or 0
        with self.scale.handler_block(self.scale_handler):
            self.scale.set_value(level)
        if self.when:
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
        warmth.set_level(level)
        if not self.when:
            return
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


class SyncGroup(Adw.PreferencesGroup):
    """Which screens move together: several monitors, and the laptop screen."""

    def __init__(self, several_monitors, has_laptop_screen):
        super().__init__(title="Sync")
        config = ddc.load_config()
        if several_monitors:
            monitors = Gtk.Switch(active=config["sync_monitors"], valign=Gtk.Align.CENTER)
            monitors.connect("notify::active", lambda switch, _: ddc.update_config(sync_monitors=switch.get_active()))
            row = Adw.ActionRow(title="Keep monitors in sync", activatable_widget=monitors,
                                subtitle="Moving one monitor's brightness or contrast moves the others too")
            row.add_suffix(monitors)
            self.add(row)
        if has_laptop_screen:
            self._laptop_rows(config)

    def _laptop_rows(self, config):
        switch = Gtk.Switch(active=config["sync_laptop"], valign=Gtk.Align.CENTER)
        switch.connect("notify::active", self._toggled)
        row = Adw.ActionRow(title="Keep the laptop screen in sync", activatable_widget=switch,
                            subtitle="Same brightness as the monitors; its keys change them too")
        row.add_suffix(switch)
        self.add(row)

        # Screens of different makes look different at the same level.
        offset = Gtk.SpinButton.new_with_range(-50, 50, 5)
        offset.set_value(config["laptop_offset"])
        offset.set_valign(Gtk.Align.CENTER)
        offset.connect("value-changed", self._offset_changed)
        self.offset_row = Adw.ActionRow(title="Laptop brighter by", sensitive=config["sync_laptop"],
                                        subtitle="For screens that look different at the same level. "
                                                 "Below 0 keeps the laptop dimmer.")
        self.offset_row.add_suffix(offset)
        self.add(self.offset_row)

    def _toggled(self, switch, _):
        ddc.update_config(sync_laptop=switch.get_active())
        self.offset_row.set_sensitive(switch.get_active())

    def _offset_changed(self, spin):
        ddc.update_config(laptop_offset=spin.get_value_as_int())
        # Show the new offset straight away, from the monitors' current level.
        state = ddc.load_state()
        for bus in state.get("buses") or []:
            value = state.get("brightness", {}).get(str(bus))
            if value is not None:
                percent = ddc.to_percent(value, ddc.recorded_maximum(state, bus, ddc.BRIGHTNESS))
                laptop.set_brightness(ddc.laptop_level(percent), wait=False)
                return


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
        if self.warmth_group and not self.night_light:  # Dimly's own tint is kept in the state
            self.warmth_group.show_settings()

    def sync_monitors(self, source, code, percent):
        """Bring every other monitor to the level just set on one, in percent."""
        for group in self.monitor_groups.values():
            if group is not source:
                group.follow(code, percent)

    def set_warmth(self, level):
        """Set the warmth from a preset, and show it."""
        warmth.set_level(level, now=True)
        if self.warmth_group:
            self.warmth_group.show_settings()

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
            codes = [*ddc.FEATURE_NAMES, ddc.RED_GAIN, ddc.BLUE_GAIN]
            found = [(bus, name, {code: ddc.read_vcp(bus, code) for code in codes})
                     for bus, name in ddc.detect_displays()]
            # Share what was read, so the top-bar menu shows the same levels.
            for bus, _, values in found:
                for code, reading in values.items():
                    if reading and code in ddc.FEATURE_NAMES:
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
        can_warm = warmth.available()
        self.warmth_group = WarmthGroup(self.night_light) if can_warm else None
        if self.warmth_group:
            page.add(self.warmth_group)
        if len(found) > 1 or self.has_laptop_screen:
            page.add(SyncGroup(len(found) > 1, self.has_laptop_screen))
        page.add(PresetsGroup(self._presets_changed, can_warm))
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
            secondary_text="Sharpness and other settings go back to factory values. "
                           "Brightness, contrast and colour stay as they are.",
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
        what = ddc.FEATURE_NAMES.get(code, "colour")
        self.toasts.add_toast(Adw.Toast(title=f"{name} didn't accept {what} {value}. Try again."))


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
