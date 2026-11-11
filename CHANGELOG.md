# Changelog

All notable changes to Dimly. Versions follow [semantic versioning](https://semver.org/).

## 0.5.0 – 2026-11-11

- **Presets set brightness, contrast and warmth together**, from the window, the menu,
  the shortcuts and the schedule, so Evening and Night dim, soften and warm every screen.
  Edit each preset under **Presets**; a preset can also keep contrast or warmth as they
  are. Presets from earlier versions keep their brightness and get the new contrast and
  warmth defaults. A preset's warmth shows at once, rather than waiting for sunset.
- **Warmth without a night light:** where the desktop has none, as on older Linux Mint,
  Dimly tints the screens itself on X11, with no extra package. The background service
  puts the tint back after logging in, waking up or plugging in a screen.
- On Linux Mint, the panel menu's warmth slider goes through Dimly's background service,
  so it appears whichever way the warmth is made.
- **Colour balance per monitor:** a Colour slider in each monitor's section, from cooler
  to warmer, for matching monitors of different makes. It moves red and blue in opposite
  directions around that monitor's own values, and a button puts them back. The first
  time, it switches a monitor that needs it to its user colour mode.
- **Keep monitors in sync:** with two or more monitors, moving one monitor's slider in
  the window moves the others to the same level, each on its own range. Turn it off to
  set them one by one; newly connected monitors then keep their own level too. It sits
  in a new **Sync** section, with the laptop screen's sync and offset when there's a laptop.
- **Laptop brighter by:** keeps the laptop screen a set amount brighter (or dimmer) than
  the monitors while in sync, for screens that look different at the same level.
- **Factory reset keeps your colour:** the colour mode and red, green and blue gains are
  put back after a reset, along with brightness and contrast.
- The website's download button fetches the newest `.deb` itself, every time: each
  release also carries a copy named `dimly_latest_all.deb`.

## 0.4.0 – 2026-11-11

- **Warmth:** a slider for less blue light on every screen, in the window and the menu.
  It uses the desktop's own night light, from sunset to sunrise or all the time, and
  previews the warmth while you choose during the day.
- **Linux Mint:** the menu is also a Cinnamon panel applet, added to Mint's panel by itself.
- The top-bar menu works on GNOME 45 to 50 (Ubuntu 24.04 to 26.04). GNOME 45 changed how
  extensions are written, so Dimly ships both versions and the package links the one your
  GNOME needs, again whenever GNOME is upgraded.
- Choose where the menu sits in the top bar: right, next to the clock, or left. It moves
  straight away.
- The window's top-bar switch shows what GNOME is actually doing with the menu (showing,
  needs a log out, not supported, or the error), and the menu says when no adjustable
  monitor is connected.

## 0.3.0 – 2026-11-10

- Top-bar menu with brightness, contrast and presets, so you don't need to open the window.
- The background service keeps the schedule itself and sleeps until the next switch,
  instead of a timer starting Dimly every 5 minutes. It also catches up after sleep.
- The window's contrast slider follows changes made elsewhere, and a small link at the
  bottom of the window leads to the latest version.
- Fixed: the "one command at a time" lock could stop working when two threads used it.
- Fixed: Dimly's state file could be corrupted when two parts saved it at once. It now
  lives in `~/.local/state/dimly`, where cache cleaners don't remove it.
- Fixed: one failed monitor write could stop a slider working until you logged out.
- Fixed: with laptop sync on, monitor levels below 5% were pulled back up to 5%.
- Fixed: on a fresh install the top-bar sliders stayed hidden until the next login.
- Fixed: monitors whose brightness range isn't 0–100 got wrong levels from presets
  and the top bar.
- Fixed: keyboard shortcuts broke if Dimly's path contained a space.
- Fixed: a cut-off description in the window on narrow windows.
- Tests, continuous integration, and the `.deb` attached to releases automatically.
- Contributing guide, code of conduct, security policy, and issue and pull request templates.
- Website: Upgrading and Uninstalling sections, direct download links, a "star on GitHub"
  line, and a new "midnight and rose quartz" look with a matching app icon.

## 0.2.0 – 2026-11-09

- Sliders follow changes made with the shortcuts, the schedule or the laptop's keys.
- Preset levels can be edited in the window.

## 0.1.0 – 2026-11-09

- First release: brightness and contrast for external monitors over DDC/CI, laptop sync,
  keyboard shortcuts, a time-of-day schedule, hot-plugging and factory reset.
