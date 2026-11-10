# Security

## Reporting a vulnerability

Please don't open a public issue for a security problem. Use GitHub's
[private vulnerability reporting](https://github.com/saikatnjm/dimly/security/advisories/new)
instead. You'll get a reply as soon as possible, and credit in the release notes
if you'd like it.

## What Dimly can access

So you can judge the risk:

- **Monitor connections.** The package installs a udev rule
  (`/usr/lib/udev/rules.d/60-dimly-i2c.rules`) that gives the person sitting at
  the computer access to the I2C buses of graphics cards only. That's what lets
  Dimly talk to monitors without root. It doesn't cover other I2C devices.
- **No network.** Dimly never connects to the internet. (The website's download
  button asks GitHub for the latest release, but that's the web page, not the app.)
- **No root at run time.** Everything runs as your user. Only installing the
  package needs `sudo`.
- **Your account's settings.** It adds two GNOME keyboard shortcuts, turns on its
  top-bar extension and enables two systemd user units. `dimly --remove-setup`
  undoes all of that.

## Supported versions

Only the latest release gets fixes.
