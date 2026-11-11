/* Dimly's top-bar menu: brightness and contrast sliders and presets for
 * external monitors.
 *
 * The monitor work happens in Dimly's background service, reached over D-Bus.
 * It sends one command at a time to each monitor, so dragging a slider here
 * can't pile up commands that collide on the cable.
 */
'use strict';

const { Clutter, Gio, GLib, GObject, Shell, St } = imports.gi;
const Main = imports.ui.main;
const PanelMenu = imports.ui.panelMenu;
const PopupMenu = imports.ui.popupMenu;
const Slider = imports.ui.slider;
const ExtensionUtils = imports.misc.extensionUtils;

const Me = ExtensionUtils.getCurrentExtension();

const APP_ID = 'io.github.saikatnjm.Dimly';
const BUS_NAME = 'io.github.saikatnjm.Dimly.Service';
const OBJECT_PATH = '/io/github/saikatnjm/Dimly/Service';
const PRESETS = ['Night', 'Evening', 'Day', 'Max'];
const CONFIG_FILE = GLib.build_filenamev([GLib.get_user_config_dir(), 'dimly', 'config.json']);
// Where the menu goes, chosen in Dimly's window, as [part of the top bar, index in it]:
// left of the system icons, just after the clock, or just after Activities.
const PLACES = { right: ['right', 0], center: ['center', 1], left: ['left', 1] };
// Warmth is GNOME's night light, for every screen: 0 is off, 100 the warmest.
const NIGHT_LIGHT_SCHEMA = 'org.gnome.settings-daemon.plugins.color';
const COOLEST = 6500, WARMEST = 1700;  // kelvin at warmth 0 and 100
// After you move a slider, outside updates wait this long, so a change still on
// its way back from the monitor doesn't pull the slider from under you.
const LOCAL_CHANGE_GRACE_MS = 2000;

const ServiceInterface = `<node>
  <interface name="io.github.saikatnjm.Dimly.Service">
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
  </interface>
</node>`;
const ServiceProxy = Gio.DBusProxy.makeProxyWrapper(ServiceInterface);

function readPosition() {
    try {
        const [, bytes] = GLib.file_get_contents(CONFIG_FILE);
        const position = JSON.parse(new TextDecoder().decode(bytes)).top_bar_position;
        return Object.prototype.hasOwnProperty.call(PLACES, position) ? position : 'right';
    } catch (e) {
        return 'right';  // no settings file yet, or one being rewritten
    }
}

function extensionIcon(name) {
    return Gio.icon_new_for_string(`${Me.path}/icons/${name}.svg`);
}

const SliderItem = GObject.registerClass(
class SliderItem extends PopupMenu.PopupBaseMenuItem {
    _init(gicon, name, onChange) {
        super._init({ activate: false });
        this._onChange = onChange;
        this._sent = -1;
        this.lastLocalChange = 0;

        this.add_child(new St.Icon({ gicon, style_class: 'popup-menu-icon' }));
        this._slider = new Slider.Slider(0);
        this._slider.accessible_name = name;
        this._slider.x_expand = true;
        this._changedId = this._slider.connect('notify::value', () => this._sliderChanged());
        this.add_child(this._slider);
        this._label = new St.Label({ style_class: 'dimly-value', y_align: Clutter.ActorAlign.CENTER });
        this.add_child(this._label);

        // As in GNOME's own brightness slider: the whole row drags the slider.
        this.connect('button-press-event', (actor, event) => this._slider.startDragging(event));
        this.connect('key-press-event', (actor, event) => this._slider.emit('key-press-event', event));
        this.connect('scroll-event', (actor, event) => this._slider.emit('scroll-event', event));
    }

    _sliderChanged() {
        const value = Math.round(this._slider.value * 100);
        this._label.text = `${value}`;
        this.lastLocalChange = Date.now();
        if (value !== this._sent) {
            this._sent = value;
            this._onChange(value);
        }
    }

    // Shows a level set elsewhere, without sending it back out. -1 means unknown.
    setValue(value) {
        this.visible = value >= 0;
        if (value < 0)
            return;
        this._sent = value;
        this._label.text = `${value}`;
        this._slider.block_signal_handler(this._changedId);
        this._slider.value = value / 100;
        this._slider.unblock_signal_handler(this._changedId);
    }
});

const DimlyIndicator = GObject.registerClass(
class DimlyIndicator extends PanelMenu.Button {
    _init() {
        super._init(0.0, 'Dimly');
        this.add_child(new St.Icon({ gicon: extensionIcon('dimly-symbolic'), style_class: 'system-status-icon' }));

        this._brightness = new SliderItem(new Gio.ThemedIcon({ name: 'display-brightness-symbolic' }),
            'Brightness', value => this._call('SetBrightness', value));
        this._contrast = new SliderItem(extensionIcon('dimly-contrast-symbolic'),
            'Contrast', value => this._call('SetContrast', value));
        this._warmth = new SliderItem(new Gio.ThemedIcon({ name: 'weather-clear-night-symbolic' }),
            'Warmth', value => this._setWarmth(value));
        // Gio.Settings aborts GNOME Shell on a missing schema, so look it up first.
        const source = Gio.SettingsSchemaSource.get_default();
        this._nightLight = source && source.lookup(NIGHT_LIGHT_SCHEMA, true)
            ? new Gio.Settings({ schema_id: NIGHT_LIGHT_SCHEMA }) : null;
        this._nightLightChangedId = this._nightLight
            ? this._nightLight.connect('changed', () => this._showWarmth()) : 0;
        this._previewId = 0;
        this._warmthCatchUpId = 0;
        this._presets = this._presetItem();
        this._unavailable = new PopupMenu.PopupMenuItem("Dimly's background service isn't running",
            { reactive: false });
        // Dimly only adjusts external monitors; a laptop's own screen is GNOME's job.
        this._noMonitor = new PopupMenu.PopupMenuItem('No adjustable monitor found', { reactive: false });
        this.menu.addMenuItem(this._brightness);
        this.menu.addMenuItem(this._contrast);
        this.menu.addMenuItem(this._warmth);
        this.menu.addMenuItem(this._presets);
        this.menu.addMenuItem(this._unavailable);
        this.menu.addMenuItem(this._noMonitor);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Open Dimly', () => {
            const app = Shell.AppSystem.get_default().lookup_app(`${APP_ID}.desktop`);
            if (app)
                app.activate();
        });

        this._catchUpId = 0;
        this._changedId = 0;
        this._ownerId = 0;
        // GNOME 42 turns extensions off at every screen lock, so a reply can arrive
        // after this menu is gone: the cancellable and _destroyed guard against that.
        this._destroyed = false;
        this._cancellable = new Gio.Cancellable();
        this._proxy = new ServiceProxy(Gio.DBus.session, BUS_NAME, OBJECT_PATH, (proxy, error) => {
            if (this._destroyed)
                return;
            if (error) {
                logError(error, 'Dimly');
                return;
            }
            this._changedId = proxy.connectSignal('Changed',
                (_proxy, _sender, [brightness, contrast]) => this._show(brightness, contrast));
            this._ownerId = proxy.connect('notify::g-name-owner', () => this._refresh());
            this._refresh();
        }, this._cancellable, Gio.DBusProxyFlags.DO_NOT_AUTO_START);
        this.menu.connect('open-state-changed', (menu, open) => {
            if (open)
                this._refresh();
        });
        this._setAvailable(false);
        this._showWarmth();
    }

    _setWarmth(level) {
        if (this._previewId) {
            GLib.source_remove(this._previewId);
            this._previewId = 0;
        }
        if (level <= 0) {
            this._nightLight.set_boolean('night-light-enabled', false);
            return;
        }
        this._nightLight.set_uint('night-light-temperature', Math.round(COOLEST - (COOLEST - WARMEST) * level / 100));
        this._nightLight.set_boolean('night-light-enabled', true);
        // Show the warmth even if the night light isn't due yet, once you pause.
        this._previewId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
            this._previewId = 0;
            Gio.DBus.session.call('org.gnome.SettingsDaemon.Color', '/org/gnome/SettingsDaemon/Color',
                'org.gnome.SettingsDaemon.Color', 'NightLightPreview', new GLib.Variant('(u)', [3]),
                null, Gio.DBusCallFlags.NONE, -1, null, null);
            return GLib.SOURCE_REMOVE;
        });
    }

    _showWarmth() {
        if (!this._nightLight) {
            this._warmth.setValue(-1);  // no night light on this desktop
            return;
        }
        const sinceLocal = Date.now() - this._warmth.lastLocalChange;
        if (sinceLocal < LOCAL_CHANGE_GRACE_MS) {
            // Probably your own drag coming back; look again once you've let go.
            if (!this._warmthCatchUpId) {
                this._warmthCatchUpId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, LOCAL_CHANGE_GRACE_MS - sinceLocal + 50, () => {
                    this._warmthCatchUpId = 0;
                    this._showWarmth();
                    return GLib.SOURCE_REMOVE;
                });
            }
            return;
        }
        const kelvin = this._nightLight.get_uint('night-light-temperature');
        const level = Math.max(1, Math.min(100, Math.round((COOLEST - kelvin) * 100 / (COOLEST - WARMEST))));
        this._warmth.setValue(this._nightLight.get_boolean('night-light-enabled') ? level : 0);
    }

    _presetItem() {
        const item = new PopupMenu.PopupBaseMenuItem({ activate: false, can_focus: false });
        const box = new St.BoxLayout({ x_expand: true, style_class: 'dimly-presets' });
        for (const name of PRESETS) {
            const button = new St.Button({
                label: name, style_class: 'button dimly-preset', x_expand: true, can_focus: true,
            });
            button.connect('clicked', () => this._call('ApplyPreset', name));
            box.add_child(button);
        }
        item.add_child(box);
        return item;
    }

    _call(method, ...args) {
        if (!this._proxy.g_name_owner)
            return;
        this._proxy[`${method}Remote`](...args, (result, error) => {
            if (error)
                logError(error, `Dimly: ${method}`);
        });
    }

    _setAvailable(available) {
        this._unavailable.visible = !available;
        if (!available) {
            this._brightness.visible = false;
            this._contrast.visible = false;
            this._presets.visible = false;
            this._noMonitor.visible = false;
        }
    }

    _refresh() {
        const available = Boolean(this._proxy.g_name_owner);
        this._setAvailable(available);
        if (!available)
            return;
        this._proxy.GetStateRemote((result, error) => {
            if (!error && !this._destroyed)
                this._show(...result);
        });
    }

    _show(brightness, contrast) {
        const sinceLocal = Date.now() - Math.max(this._brightness.lastLocalChange, this._contrast.lastLocalChange);
        if (sinceLocal < LOCAL_CHANGE_GRACE_MS) {
            // Catch up once you've let go, with whatever the monitor has then.
            if (!this._catchUpId) {
                this._catchUpId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, LOCAL_CHANGE_GRACE_MS - sinceLocal + 50, () => {
                    this._catchUpId = 0;
                    this._refresh();
                    return GLib.SOURCE_REMOVE;
                });
            }
            return;
        }
        this._brightness.setValue(brightness);
        this._contrast.setValue(contrast);
        this._presets.visible = brightness >= 0;
        this._noMonitor.visible = brightness < 0 && contrast < 0;
    }

    destroy() {
        this._destroyed = true;
        this._cancellable.cancel();
        if (this._nightLightChangedId)
            this._nightLight.disconnect(this._nightLightChangedId);
        if (this._previewId)
            GLib.source_remove(this._previewId);
        if (this._warmthCatchUpId)
            GLib.source_remove(this._warmthCatchUpId);
        if (this._catchUpId)
            GLib.source_remove(this._catchUpId);
        if (this._changedId)
            this._proxy.disconnectSignal(this._changedId);
        if (this._ownerId)
            this._proxy.disconnect(this._ownerId);
        super.destroy();
    }
});

class Extension {
    enable() {
        this._position = readPosition();
        this._place();
        // Move the menu as soon as its position changes in Dimly's window.
        this._configMonitor = Gio.File.new_for_path(CONFIG_FILE).monitor_file(Gio.FileMonitorFlags.NONE, null);
        this._configChangedId = this._configMonitor.connect('changed', () => {
            const position = readPosition();
            if (position !== this._position) {
                this._position = position;
                this._place();
            }
        });
    }

    _place() {
        this._indicator?.destroy();
        this._indicator = null;
        const [box, index] = PLACES[this._position];
        const indicator = new DimlyIndicator();
        Main.panel.addToStatusArea(Me.metadata.uuid, indicator, index, box);
        this._indicator = indicator;
    }

    disable() {
        if (this._configMonitor) {
            this._configMonitor.disconnect(this._configChangedId);
            this._configMonitor.cancel();
            this._configMonitor = null;
        }
        this._indicator?.destroy();
        this._indicator = null;
    }
}

function init() {
    return new Extension();
}
