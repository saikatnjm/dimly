# Changelog

All notable changes to Dimly. Versions follow [semantic versioning](https://semver.org/).

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
