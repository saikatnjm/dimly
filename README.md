# Dimly

**English** · [বাংলা](README.bn.md) · [Website](https://saikatnjm.github.io/dimly/)

Easy-on-the-eyes brightness for external monitors, in step with your laptop screen.

Linux desktops only dim the laptop's own screen. Dimly controls an external
monitor's real backlight over the cable (DDC/CI), so you can stop reaching for
its buttons, and keeps it at the same level as your laptop all day.

## Features

- **Brightness and contrast sliders** for each monitor, plus one-click
  **Night, Evening, Day and Max** presets. Each preset sets brightness, contrast and
  warmth together, and you choose its levels; a preset can also keep contrast or
  warmth as they are. The sliders follow changes made with the keys, shortcuts or schedule.
- **Warmth**: one slider for less blue light on every screen, through the desktop's
  own night light, from sunset to sunrise or all the time. Where the desktop has no
  night light (as on older Linux Mint), Dimly tints the screens itself on X11.
- **Laptop sync**: the laptop's brightness keys change the external monitors too,
  and the sliders change both. If the laptop looks dimmer than the monitor at the
  same level, keep it a few steps brighter with **Laptop brighter by**.
- **Colour balance** per monitor: one slider from cooler to warmer, for matching
  monitors of different makes. It moves the monitor's red and blue around its own
  values, and the button next to it puts them back.
- **Top-bar menu**: brightness, contrast, warmth and the presets one click away,
  from the Dimly icon in the top bar (on Linux Mint, in the panel). Put it on the
  right, next to the clock or on the left.
- **Keyboard shortcuts**: <kbd>Super</kbd>+<kbd>F6</kbd> brighter,
  <kbd>Super</kbd>+<kbd>F5</kbd> dimmer.
- **Schedule**: switches preset by time of day, so the screens get dimmer and warmer
  as the evening goes on. A change you make yourself lasts until the next switch.
- **Several monitors** move together: one monitor's slider moves the others too, each
  on its own range. Turn off **Keep monitors in sync** to set them one by one; the
  shortcuts, menu, presets and schedule still change them all.
- **Hot-plug**: a newly connected monitor is brought to the current level, while the
  monitors are kept in sync.
- **Factory reset** for monitors whose backlight has stopped responding to
  brightness, keeping your brightness, contrast and colour settings.

Built for GNOME 42 to 50 (Ubuntu 22.04 to 26.04) and Linux Mint's Cinnamon, where
the menu lives in the panel. On Cinnamon, laptop sync and the automatic keyboard
shortcuts aren't available yet; you can bind `dimly --up` and `dimly --down` to
keys in Mint's Keyboard settings.

## Install

### Ubuntu / Debian package

Download [dimly_latest_all.deb](https://github.com/saikatnjm/dimly/releases/latest/download/dimly_latest_all.deb), always the newest
version (each version is also on the [releases page](https://github.com/saikatnjm/dimly/releases)),
then, in the folder you saved it to:

```bash
sudo apt install ./dimly_latest_all.deb
```

Log out and back in once, so your account can reach monitors and GNOME picks up
the top-bar menu. Then open **Dimly** from the app menu. The first start adds the
keyboard shortcuts, starts the background services and shows the top-bar menu.

### Upgrading

Download the new `.deb` and install it the same way. There's no need to uninstall
first: apt replaces the old version, and your settings, schedule and presets are
kept. Then log out and back in, so GNOME and the background service pick up the
new version.

### Uninstalling

```bash
dimly --remove-setup && sudo apt remove dimly
```

The first part removes what Dimly set up for your account: the keyboard shortcuts,
the top-bar menu and the background services. Run it before removing the package,
because afterwards the `dimly` command is gone. Your settings stay in
`~/.config/dimly`, in case you reinstall; to delete them too:

```bash
rm -rf ~/.config/dimly ~/.cache/dimly ~/.local/state/dimly
```

If you've already removed the package, delete the two "Dimly" entries in
GNOME Settings → Keyboard → Custom Shortcuts. Nothing else is left running.

### From source

You need `ddcutil`, Python 3.10 or later, GTK 4 and libadwaita:

```bash
sudo apt install ddcutil python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
```

Then do the steps under [Setting up](#setting-up), and run `./install.sh`. It
installs into `~/.local` for your account only. Run it again after changing the
code; `./uninstall.sh` removes it.

## Setting up

The package does steps 1 and 2 for you.

1. **Load the `i2c-dev` module** at every boot:

   ```bash
   echo i2c-dev | sudo tee /etc/modules-load.d/dimly.conf && sudo modprobe i2c-dev
   ```

2. **Let your account reach monitors.** Copy Dimly's udev rule and reload:

   ```bash
   sudo cp data/60-dimly-i2c.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=i2c-dev
   ```

   Then log out and back in.

3. **Turn on DDC/CI in the monitor's own menu**, if it has the option. Most
   monitors have it on by default.

To check that it works, this should print the monitor's current brightness:

```bash
ddcutil getvcp 10
```

## Using it

| To | Do this |
|---|---|
| Change brightness on all screens | Laptop brightness keys, the Dimly icon in the top bar, the Brightness slider, or <kbd>Super</kbd>+<kbd>F6</kbd> / <kbd>F5</kbd> |
| Hide the top-bar icon | Turn off **Show Dimly in the top bar** in the window |
| Move the top-bar icon | Pick **Right**, **Next to the clock** or **Left** under **Position** |
| Change what a preset does | Open it under **Presets** and set its brightness, contrast and warmth. Below 0 means **Keep**: the preset leaves that setting alone |
| Change the schedule | Edit the times and presets in the **Schedule** section |
| Change the shortcut keys | GNOME Settings → Keyboard → Custom Shortcuts |
| Keep the laptop screen separate | Turn off **Keep the laptop screen in sync** under **Sync** |
| Set each monitor on its own | Turn off **Keep monitors in sync** (shown with two or more monitors) |
| Make the laptop match the monitor | Set **Laptop brighter by**, e.g. 10; below 0 keeps it dimmer |
| Match a monitor's colour to another screen | Move its **Colour** slider; the button next to it undoes it |

From a terminal:

```bash
dimly --up
```

The other options are `--down`, `--set 40` and `--preset Night`. See
`dimly --help` for all of them.

### Settings file

Settings live in `~/.config/dimly/config.json`. The window edits all of them, including
the presets, except how much each shortcut press changes. To change that, edit
the file:

```json
{
  "step": 10
}
```

## Troubleshooting

- **"No adjustable monitor found"**: check that DDC/CI is on in the monitor's
  menu. Many USB-C docks and DisplayLink adapters don't pass DDC/CI through:
  try connecting the monitor directly with HDMI or DisplayPort.
- **The brightness number changes but the screen doesn't**: some monitors get
  stuck ignoring brightness. Use **Factory reset** in the monitor's section.
- **The monitor follows a second or so after the laptop**: that's normal.
  Monitors take about half a second for each change.

## How it works

Dimly runs `ddcutil` for every monitor command. Each call takes a shared lock,
because overlapping commands collide on the cable and slow monitors drop them.
The laptop screen is controlled through GNOME's power service, and a small
background service follows the laptop's brightness and watches for new
monitors.

## Licence

MIT. See [LICENSE](LICENSE).
