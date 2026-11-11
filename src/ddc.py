"""DDC/CI access through ddcutil, plus the settings and state shared by the
window, the keyboard shortcuts and the background service.

Every ddcutil call holds one lock, across threads and processes, so nothing
talks to a monitor at the same time as anything else: overlapping writes
collide on the I2C bus and slow monitors drop them.
"""

import copy
import fcntl
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

FACTORY_RESET = 0x04
BRIGHTNESS = 0x10
CONTRAST = 0x12
FEATURE_NAMES = {BRIGHTNESS: "brightness", CONTRAST: "contrast"}
PRESET_NAMES = ["Night", "Evening", "Day", "Max"]
# Where the top-bar menu sits: in the right, centre (by the clock) or left part of the bar.
TOP_BAR_POSITIONS = ["right", "center", "left"]

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "dimly"
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dimly" / "config.json"
# State, unlike a cache, must survive cache cleaners: it remembers e.g. that you
# turned the top-bar menu off. Versions before 0.3.0 kept it in CACHE_DIR.
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "dimly"
STATE_FILE = STATE_DIR / "state.json"
OLD_STATE_FILE = CACHE_DIR / "state.json"
LOCK_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or CACHE_DIR) / "dimly"
DEFAULT_CONFIG = {
    "step": 10,
    "sync_laptop": True,
    # Levels from 0 to 100. Change them in the config file.
    "presets": {"Night": 10, "Evening": 30, "Day": 60, "Max": 100},
    "schedule_enabled": True,
    "schedule": [["08:00", "Day"], ["18:00", "Evening"], ["22:00", "Night"]],
    "top_bar_position": "right",
}


_held = threading.local()


@contextmanager
def _lock(name):
    """Hold an exclusive lock shared by all of Dimly's processes and threads.

    Re-entrant within one thread. flock() locks belong to each open() of the file,
    so two threads of one process exclude each other just like two processes do.
    """
    depth = getattr(_held, name, 0)
    if depth:
        setattr(_held, name, depth + 1)
        try:
            yield
        finally:
            setattr(_held, name, depth)
        return
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_DIR / f"{name}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        setattr(_held, name, 1)
        try:
            yield
        finally:
            setattr(_held, name, 0)


def monitor_lock():
    """Hold the lock that every ddcutil call needs."""
    return _lock("ddcutil")


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
        if before[BRIGHTNESS] is None:
            return False  # not answering at all, so a reset would only lose its levels
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


def level(percent):
    """Return a new_value function for set_brightness that sets this level, in percent."""
    return lambda _, maximum: round(percent * maximum / 100)


def to_percent(value, maximum):
    return round(100 * value / maximum) if maximum else value


def from_percent(percent, maximum):
    return round(percent * maximum / 100)


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
    levels = {}
    maxima = {}
    for bus in buses:
        reading = read_vcp(bus, BRIGHTNESS)
        if reading is None:
            continue
        current, maximum = reading
        value = max(0, min(maximum, new_value(current, maximum)))
        if value == current or write_vcp(bus, BRIGHTNESS, value):
            results.append((value, maximum))
            levels[bus] = value
            maxima[bus] = maximum
    record_levels(BRIGHTNESS, levels, maxima)
    return results


def record_levels(code, levels, maxima=None):
    """Note {bus: value} as the monitors' current brightness or contrast, and
    optionally {bus: maximum}. The window and the top-bar menu watch this, so
    their sliders follow changes made elsewhere."""
    if not levels:
        return
    key = FEATURE_NAMES[code]

    def merge(state):
        state[key] = {**state.get(key, {}), **{str(bus): value for bus, value in levels.items()}}
        if maxima:
            # Kept per feature, as e.g. {"5": {"brightness": 100, "contrast": 100}}.
            recorded = state.setdefault("maxima", {})
            for bus, maximum in maxima.items():
                recorded.setdefault(str(bus), {})[key] = maximum

    modify_state(merge)


def recorded_maximum(state, bus, code):
    """The monitor's maximum for a feature, as last read; most monitors use 100."""
    return state.get("maxima", {}).get(str(bus), {}).get(FEATURE_NAMES[code], 100)


class Writer(threading.Thread):
    """Sends the latest requested value per feature to one monitor, one write at a time.

    While a slider is dragged, only the newest position is sent once the monitor
    is free, so it never falls behind. on_error(code, value) is called from this
    thread when the monitor keeps refusing a value.
    """

    def __init__(self, bus, on_error=None):
        super().__init__(daemon=True)
        self.bus = bus
        self.on_error = on_error
        self.pending = {}
        self.cond = threading.Condition()
        self.start()

    def request(self, code, value):
        with self.cond:
            self.pending[code] = value
            self.cond.notify()

    def run(self):
        while True:
            with self.cond:
                while not self.pending:
                    self.cond.wait()
                code, value = self.pending.popitem()
            try:
                written = self._write(code, value)
            except Exception as error:  # a hung or vanished monitor must not stop this writer for good
                print(f"Writing {FEATURE_NAMES.get(code, code)} {value} to bus {self.bus} failed: {error}", flush=True)
                written = False
            if not written and self.on_error:
                self.on_error(code, value)

    def _write(self, code, value):
        for _ in range(3):
            if write_vcp(self.bus, code, value):
                record_levels(code, {self.bus: value})
                return True
            with self.cond:
                if code in self.pending:  # a newer value replaces this one
                    return True
            time.sleep(0.3)
        return False


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
    entries = sorted((parse_time(entry[0]), entry[1]) for entry in schedule
                     if isinstance(entry, (list, tuple)) and len(entry) == 2
                     and isinstance(entry[0], str) and parse_time(entry[0]) and entry[1] in PRESET_NAMES)
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


def seconds_until_next_switch(schedule, now):
    """Seconds from `now` until the schedule's next switch time, or None if it has none."""
    times = sorted({parse_time(entry[0]) for entry in schedule
                    if isinstance(entry, (list, tuple)) and len(entry) == 2
                    and isinstance(entry[0], str) and parse_time(entry[0])})
    if not times:
        return None
    today = now.replace(second=0, microsecond=0)
    for day in (0, 1):
        for at in times:
            hour, minute = map(int, at.split(":"))
            switch = today.replace(hour=hour, minute=minute) + timedelta(days=day)
            if switch > now:
                return max(1, int((switch - now).total_seconds()))
    return None


def _load_json(path, default):
    data = copy.deepcopy(default)
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return data
    if isinstance(loaded, dict):
        data.update(loaded)
    return data


def _save_json(path, data):
    """Replace the file in one step, through a temporary file of this write's own,
    so a reader never sees half a file and two writers can't mix theirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def load_config():
    """The settings, with anything of the wrong shape (say, from hand-editing) replaced by its default."""
    config = _load_json(CONFIG_FILE, DEFAULT_CONFIG)
    for key, default in DEFAULT_CONFIG.items():
        if type(config.get(key)) is not type(default):
            config[key] = copy.deepcopy(default)
    config["presets"] = {name: value for name, value in config["presets"].items()
                         if name in PRESET_NAMES and type(value) is int}
    if config["top_bar_position"] not in TOP_BAR_POSITIONS:
        config["top_bar_position"] = DEFAULT_CONFIG["top_bar_position"]
    config["schedule"] = [entry for entry in config["schedule"]
                          if isinstance(entry, list) and len(entry) == 2 and all(isinstance(x, str) for x in entry)]
    return config


def presets():
    """Return {name: level} for the presets, filling in any the config file leaves out."""
    return {**DEFAULT_CONFIG["presets"], **load_config()["presets"]}


def update_config(**changes):
    """Save just these settings, so parts of the window don't overwrite each other's changes."""
    with _lock("config"):
        _save_json(CONFIG_FILE, {**load_config(), **changes})


def load_state():
    if not STATE_FILE.exists() and OLD_STATE_FILE.exists():
        return _load_json(OLD_STATE_FILE, {})
    return _load_json(STATE_FILE, {})


def modify_state(change):
    """Load the state, let change(state) edit it, and save it, all under one lock,
    so changes made at the same moment by other threads or processes aren't lost."""
    with _lock("state"):
        state = load_state()
        change(state)
        _save_json(STATE_FILE, state)
        OLD_STATE_FILE.unlink(missing_ok=True)  # migrated by the save above


def update_state(**changes):
    modify_state(lambda state: state.update(changes))
