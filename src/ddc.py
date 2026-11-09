"""DDC/CI access through ddcutil, plus the settings shared by the window,
the keyboard shortcuts and the schedule timer.

Every ddcutil call holds one lock file, so those three never talk to the
monitor at the same time: overlapping writes collide on the I2C bus and slow
monitors drop them.
"""

import copy
import fcntl
import json
import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

FACTORY_RESET = 0x04
BRIGHTNESS = 0x10
CONTRAST = 0x12
FEATURE_NAMES = {BRIGHTNESS: "brightness", CONTRAST: "contrast"}
PRESET_NAMES = ["Night", "Evening", "Day", "Max"]

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "dimly"
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dimly" / "config.json"
STATE_FILE = CACHE_DIR / "state.json"
DEFAULT_CONFIG = {
    "step": 10,
    "sync_laptop": True,
    # Levels from 0 to 100. Change them in the config file.
    "presets": {"Night": 10, "Evening": 30, "Day": 60, "Max": 100},
    "schedule_enabled": True,
    "schedule": [["08:00", "Day"], ["18:00", "Evening"], ["22:00", "Night"]],
}


_lock_depth = 0


@contextmanager
def monitor_lock():
    """Hold the lock that every ddcutil call needs. Re-entrant within one process."""
    global _lock_depth
    if _lock_depth:
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_DIR / "ddcutil.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _lock_depth = 1
        try:
            yield
        finally:
            _lock_depth = 0


def ddcutil(*args, timeout=15):
    with monitor_lock():
        return subprocess.run(["ddcutil", *args], capture_output=True, text=True, timeout=timeout)


def access_problem():
    """Return why monitors can't be reached at all, or None if nothing obvious is wrong."""
    if not shutil.which("ddcutil"):
        return "ddcutil isn't installed. Install it with: sudo apt install ddcutil"
    devices = list(Path("/dev").glob("i2c-*"))
    if not devices:
        return "The i2c-dev kernel module isn't loaded. Load it with: sudo modprobe i2c-dev"
    if not any(os.access(d, os.R_OK | os.W_OK) for d in devices):
        return ("You don't have permission to control monitors yet. If you've just installed Dimly, "
                "log out and back in. Otherwise, see \"Setting up\" in Dimly's README.")
    return None


def detect_displays():
    """Return [(bus, name)] for the displays that answer DDC/CI."""
    out = ddcutil("detect", "--brief", timeout=30).stdout
    displays = []
    for block in out.split("\n\n"):
        block = block.strip()
        # "Invalid display" blocks have no DDC/CI, e.g. a laptop's built-in panel.
        if not block.startswith("Display"):
            continue
        bus = re.search(r"/dev/i2c-(\d+)", block)
        if not bus:
            continue
        model = re.search(r"Monitor:\s*[^:\n]*:([^:\n]*)", block)
        name = model.group(1).strip() if model and model.group(1).strip() else f"Monitor on bus {bus.group(1)}"
        displays.append((int(bus.group(1)), name))
    update_state(buses=[bus for bus, _ in displays])
    return displays


def read_vcp(bus, code):
    """Return (current, max) for a feature, or None if the monitor doesn't report it."""
    res = ddcutil("--bus", str(bus), "getvcp", "--brief", f"{code:02x}")
    # Brief output looks like "VCP 10 C 35 100".
    parts = res.stdout.split()
    if res.returncode == 0 and len(parts) >= 5 and parts[2] == "C":
        return int(parts[3]), int(parts[4])
    return None


def write_vcp(bus, code, value):
    return ddcutil("--bus", str(bus), "setvcp", f"{code:02x}", str(value)).returncode == 0


def factory_reset(bus):
    """Restore the monitor's factory settings, keeping its brightness and contrast.

    This clears monitor states that ignore brightness. The reset itself turns
    brightness to full and contrast to the factory value, so the previous
    values are put back afterwards.
    """
    with monitor_lock():
        before = {code: read_vcp(bus, code) for code in FEATURE_NAMES}
        if not write_vcp(bus, FACTORY_RESET, 1):
            return False
        # While it resets, the monitor accepts commands and then ignores them,
        # so keep writing until reading back shows the value has stuck.
        for code, reading in before.items():
            if reading is None:
                continue
            for _ in range(10):
                time.sleep(1)
                if write_vcp(bus, code, reading[0]) and read_vcp(bus, code) == reading:
                    break
        time.sleep(1.5)  # it answers reads only after settling again
    return True


def set_brightness(new_value, sync_laptop=True):
    """Set every monitor's brightness to new_value(current, maximum).

    Returns [(value, maximum)] for the monitors that took it. Uses the buses
    found last time, and searches again only if none of them answers, because
    a full search takes a couple of seconds. With sync_laptop and the setting
    on, the laptop screen follows; the laptop-following service turns it off
    so the two don't keep echoing each other.
    """
    # Locked from read to write, so quick key presses each build on the last one.
    with monitor_lock():
        results = _set_brightness(new_value)
    if results and sync_laptop and load_config()["sync_laptop"]:
        import laptop
        value, maximum = results[0]
        laptop.set_brightness(round(100 * value / maximum))
    return results


def _set_brightness(new_value):
    for buses in (load_state().get("buses"), None):
        if buses is None:
            buses = [bus for bus, _ in detect_displays()]
        results = _set_brightness_on(buses, new_value)
        if results:
            return results
    return []


def set_brightness_on(buses, new_value):
    """Like set_brightness, for just these monitors and without moving the laptop screen."""
    with monitor_lock():
        return _set_brightness_on(buses, new_value)


def _set_brightness_on(buses, new_value):
    results = []
    for bus in buses:
        reading = read_vcp(bus, BRIGHTNESS)
        if reading is None:
            continue
        current, maximum = reading
        value = max(0, min(maximum, new_value(current, maximum)))
        if value == current or write_vcp(bus, BRIGHTNESS, value):
            results.append((value, maximum))
    return results


def parse_time(text):
    """Return text as "HH:MM", or None if it isn't a valid time of day."""
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text)
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def current_slot(schedule, now):
    """Return (slot_key, preset) for the schedule entry in effect at `now`, or None.

    slot_key names one occurrence of an entry, like "2026-11-09 18:00", so the
    timer can tell when a new one has started.
    """
    entries = sorted((parse_time(t), p) for t, p in schedule if parse_time(t) and p in PRESET_NAMES)
    if not entries:
        return None
    started = [e for e in entries if e[0] <= now.strftime("%H:%M")]
    if started:
        time, preset = started[-1]
        day = now.date()
    else:  # before today's first entry: yesterday's last one is still running
        time, preset = entries[-1]
        day = now.date() - timedelta(days=1)
    return f"{day} {time}", preset


def _load_json(path, default):
    data = copy.deepcopy(default)
    try:
        data.update(json.loads(path.read_text()))
    except (OSError, ValueError):
        pass
    return data


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def load_config():
    return _load_json(CONFIG_FILE, DEFAULT_CONFIG)


def presets():
    """Return {name: level} for the presets, filling in any the config file leaves out."""
    return {**DEFAULT_CONFIG["presets"], **load_config()["presets"]}


def update_config(**changes):
    """Save just these settings, so parts of the window don't overwrite each other's changes."""
    _save_json(CONFIG_FILE, {**load_config(), **changes})


def load_state():
    return _load_json(STATE_FILE, {})


def update_state(**changes):
    _save_json(STATE_FILE, {**load_state(), **changes})
