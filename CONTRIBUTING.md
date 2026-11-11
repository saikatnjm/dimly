# Contributing to Dimly

Thanks for helping. Bug reports from different monitors are especially useful,
because every monitor speaks DDC/CI a little differently.

## Reporting a problem

Open an [issue](https://github.com/saikatnjm/dimly/issues/new/choose) and pick
**Bug report**. It asks for the details that make a monitor problem fixable:
your monitor's model, how it's connected, and the output of:

```bash
ddcutil detect
```

## Working on the code

Dimly is plain Python 3.10 with GTK 4 and libadwaita, plus a GNOME Shell 42
extension in JavaScript. There's no build step.

```bash
sudo apt install ddcutil python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
git clone https://github.com/saikatnjm/dimly.git && cd dimly
./install.sh
```

`install.sh` installs into `~/.local` for your account; run it again after each
change. `./uninstall.sh` removes it. If you also have the `.deb` installed,
remove that first so the two don't mix.

### Where things are

| Path | What it does |
|---|---|
| `src/ddc.py` | Talks to monitors through `ddcutil`, one command at a time; settings and the schedule |
| `src/service.py` | The background service: laptop sync, the schedule, new monitors, and the D-Bus interface the top-bar menu uses |
| `src/notify.py` | Brightness notifications |
| `src/about.py` | Name, version and links |
| `src/gui.py` | The window |
| `src/dimly.py` | The `dimly` command and its options |
| `src/laptop.py` | The laptop screen's brightness, through GNOME |
| `src/integration.py` | Shortcuts, the top-bar menu switch and the background services |
| `extension/` | The top-bar menu (GNOME Shell extension). Edit `gnome42/extension.js`, then run `extension/make-gnome45.py` to update the GNOME 45+ version |
| `data/` | Launcher, app-store listing, icon, systemd units, udev rule |
| `packaging/build-deb.sh` | Builds the `.deb` |

### Before opening a pull request

```bash
python3 -m unittest discover -s tests -v
python3 -m pyflakes src tests
node --check extension/gnome42/extension.js
```

The same checks run automatically on every pull request.

- Keep changes small and focused, and describe how you tested them, including
  which monitor and connection you used.
- Dimly is meant to stay light: nothing new should run in the background or
  wake up periodically without a good reason.
- Match the surrounding code: short functions, comments that explain *why*.
- If you change what users see, update both `README.md` and `README.bn.md`.

## Releasing (maintainers)

1. Update `VERSION` in `src/about.py`, add the release to
   `data/io.github.saikatnjm.Dimly.metainfo.xml`, and add it to `CHANGELOG.md`.
2. Commit, then tag and push: `git tag v0.4.0 && git push origin v0.4.0`.
3. CI builds the `.deb` and attaches it to the GitHub release for that tag.

By contributing, you agree that your contributions are licensed under the
project's [MIT licence](LICENSE), and to follow the
[code of conduct](CODE_OF_CONDUCT.md).
