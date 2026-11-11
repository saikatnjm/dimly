/* Dimly's panel applet for Cinnamon (Linux Mint): brightness, contrast and
 * warmth sliders and presets. The same menu as the GNOME top-bar extension, and
 * it talks to the same background service, which does the work. For warmth the
 * service uses Cinnamon's night light, or Dimly's own tint where there's none.
 */
const Applet = imports.ui.applet;
const PopupMenu = imports.ui.popupMenu;
const { Gio, St } = imports.gi;

const APP_ID = 'io.github.saikatnjm.Dimly';
const BUS_NAME = 'io.github.saikatnjm.Dimly.Service';
const OBJECT_PATH = '/io/github/saikatnjm/Dimly/Service';
const PRESETS = ['Night', 'Evening', 'Day', 'Max'];
// After you move a slider, outside updates wait this long, so a change still on
// its way back doesn't pull the slider from under you.
const LOCAL_CHANGE_GRACE_MS = 2000;

const ServiceProxy = Gio.DBusProxy.makeProxyWrapper(`<node>
  <interface name="io.github.saikatnjm.Dimly.Service">
    <method name="GetState">
      <arg name="brightness" type="i" direction="out"/>
      <arg name="contrast" type="i" direction="out"/>
    </method>
    <method name="SetBrightness"><arg name="level" type="i" direction="in"/></method>
    <method name="SetContrast"><arg name="value" type="i" direction="in"/></method>
    <method name="ApplyPreset"><arg name="name" type="s" direction="in"/></method>
    <method name="GetWarmth"><arg name="level" type="i" direction="out"/></method>
    <method name="SetWarmth"><arg name="level" type="i" direction="in"/></method>
    <signal name="Changed">
      <arg name="brightness" type="i"/>
      <arg name="contrast" type="i"/>
    </signal>
    <signal name="WarmthChanged"><arg name="level" type="i"/></signal>
  </interface>
</node>`);

// A titled slider. Levels are 0 to 100; -1 hides it.
class Level {
    constructor(menu, title, onChange) {
        this.title = title;
        this.label = new PopupMenu.PopupMenuItem(title, { reactive: false });
        this.slider = new PopupMenu.PopupSliderMenuItem(0);
        this.lastLocalChange = 0;
        this._sent = -1;
        this._changedId = this.slider.connect('value-changed', (item, value) => {
            const level = Math.round(value * 100);
            this.lastLocalChange = Date.now();
            if (level === this._sent)
                return;  // the slider reports every pointer move; send only real changes
            this._sent = level;
            this.label.label.text = `${title}  ${level}`;
            onChange(level);
        });
        menu.addMenuItem(this.label);
        menu.addMenuItem(this.slider);
    }

    show(level) {
        this.label.actor.visible = this.slider.actor.visible = level >= 0;
        if (level < 0 || Date.now() - this.lastLocalChange < LOCAL_CHANGE_GRACE_MS)
            return;
        this._sent = level;
        this.label.label.text = `${this.title}  ${level}`;
        this.slider.setValue(level / 100);
    }
}

class DimlyApplet extends Applet.IconApplet {
    constructor(metadata, orientation, panelHeight, instanceId) {
        super(orientation, panelHeight, instanceId);
        this.set_applet_icon_symbolic_path(`${metadata.path}/icons/dimly-symbolic.svg`);
        this.set_applet_tooltip('Dimly');
        this.menuManager = new PopupMenu.PopupMenuManager(this);
        this.menu = new Applet.AppletPopupMenu(this, orientation);
        this.menuManager.addMenu(this.menu);

        this._brightness = new Level(this.menu, 'Brightness', level => this._call('SetBrightness', level));
        this._contrast = new Level(this.menu, 'Contrast', level => this._call('SetContrast', level));
        this._warmth = new Level(this.menu, 'Warmth', level => this._call('SetWarmth', level));

        this._presets = new PopupMenu.PopupBaseMenuItem({ reactive: false });
        const box = new St.BoxLayout({ style_class: 'dimly-presets' });
        for (const name of PRESETS) {
            const button = new St.Button({ label: name, style_class: 'dimly-preset', x_expand: true });
            button.connect('clicked', () => this._call('ApplyPreset', name));
            box.add_child(button);
        }
        this._presets.addActor(box, { expand: true });
        this.menu.addMenuItem(this._presets);
        this._message = new PopupMenu.PopupMenuItem('', { reactive: false });
        this.menu.addMenuItem(this._message);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this.menu.addAction('Open Dimly', () => {
            const app = Gio.DesktopAppInfo.new(`${APP_ID}.desktop`);
            if (app)
                app.launch([], null);
        });

        this._destroyed = false;
        this._cancellable = new Gio.Cancellable();
        this._proxy = new ServiceProxy(Gio.DBus.session, BUS_NAME, OBJECT_PATH, (proxy, error) => {
            if (this._destroyed || error)
                return;
            this._changedId = proxy.connectSignal('Changed',
                (_proxy, _sender, [brightness, contrast]) => this._show(brightness, contrast));
            this._warmthChangedId = proxy.connectSignal('WarmthChanged',
                (_proxy, _sender, [level]) => this._warmth.show(level));
            this._ownerId = proxy.connect('notify::g-name-owner', () => this._refresh());
            this._refresh();
        }, this._cancellable, Gio.DBusProxyFlags.DO_NOT_AUTO_START);
        this._show(-1, -1);
        this._warmth.show(-1);
    }

    on_applet_clicked() {
        if (!this.menu.isOpen)
            this._refresh();
        this.menu.toggle();
    }

    _call(method, ...args) {
        if (this._proxy.g_name_owner)
            this._proxy[`${method}Remote`](...args, () => {});
    }

    _refresh() {
        if (!this._proxy.g_name_owner) {
            this._show(-1, -1, "Dimly's background service isn't running");
            return;
        }
        this._proxy.GetStateRemote((result, error) => {
            if (!error && !this._destroyed)
                this._show(...result);
        });
        this._proxy.GetWarmthRemote((result, error) => {
            if (!error && !this._destroyed)
                this._warmth.show(result[0]);
        });
    }

    _show(brightness, contrast, message) {
        this._brightness.show(brightness);
        this._contrast.show(contrast);
        if (message)
            this._warmth.show(-1);
        this._presets.actor.visible = brightness >= 0;
        // Dimly only adjusts external monitors; a laptop's own screen is the desktop's job.
        const text = message || (brightness < 0 && contrast < 0 ? 'No adjustable monitor found' : '');
        this._message.label.text = text;
        this._message.actor.visible = text !== '';
    }

    on_applet_removed_from_panel() {
        this._destroyed = true;
        this._cancellable.cancel();
        if (this._changedId)
            this._proxy.disconnectSignal(this._changedId);
        if (this._warmthChangedId)
            this._proxy.disconnectSignal(this._warmthChangedId);
        if (this._ownerId)
            this._proxy.disconnect(this._ownerId);
        this.menu.destroy();
    }
}

function main(metadata, orientation, panelHeight, instanceId) {
    return new DimlyApplet(metadata, orientation, panelHeight, instanceId);
}
