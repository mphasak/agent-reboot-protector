"""Shared helpers: paths, config, logging, atomic JSON I/O, boot identity."""

import copy
import json
import logging
import os
import subprocess
import sys
import tempfile
import time

APP_NAME = "agent-reboot-protector"

log = logging.getLogger("arp")


def is_macos():
    return sys.platform == "darwin"


def state_dir():
    if is_macos():
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME)


def config_path():
    return os.path.join(config_dir(), "config.json")


def manifest_path():
    return os.path.join(state_dir(), "manifest.json")


def contexts_dir():
    d = os.path.join(state_dir(), "contexts")
    os.makedirs(d, exist_ok=True)
    return d


def history_dir():
    d = os.path.join(state_dir(), "history")
    os.makedirs(d, exist_ok=True)
    return d


DEFAULT_CONFIG = {
    # Terminal emulator binary and how to identify its processes/windows.
    "terminal": "ghostty",
    # Snapshot cadence for `arp watch` (safety net even without a detected
    # scheduled shutdown).
    "periodic_minutes": 5,
    # How far ahead of a detected scheduled shutdown to force a snapshot.
    "shutdown_lead_minutes": 20,
    # Poll interval of the watch loop, seconds.
    "watch_poll_seconds": 30,
    # A session file touched within this window counts as "active".
    "active_session_window_minutes": 360,
    # How many manifest snapshots to keep in history/.
    "history_keep": 30,
    "restore": {
        # Keep the shell open after the resumed agent exits.
        "keep_shell_open": True,
        # Max seconds to wait for the window manager at login.
        "max_wait_wm_seconds": 180,
        # Pause between spawning windows (ms) so window matching stays reliable.
        "spawn_delay_ms": 400,
        # Seconds to wait for a newly spawned window to appear.
        "spawn_window_timeout_seconds": 15,
    },
    # Per-agent extra args appended to every generated resume command.
    "agents": {
        "claude-code": {"extra_args": []},
        "copilot": {"extra_args": []},
        "cursor": {"extra_args": []},
    },
    # Force a specific window backend: x11 | hyprland | sway | macos | generic.
    "backend": None,
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    path = config_path()
    user = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                user = json.load(f)
        except (OSError, ValueError) as e:
            log.warning("could not read config %s: %s (using defaults)", path, e)
    return _merge(DEFAULT_CONFIG, user)


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def boot_id():
    """A string that changes on every boot of this machine."""
    if is_macos():
        try:
            out = subprocess.run(
                ["sysctl", "-n", "kern.boottime"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if out:
                return out
        except (OSError, subprocess.SubprocessError):
            pass
        return "unknown-macos-boot"
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "unknown-boot"


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def run_cmd(argv, timeout=10, check=False):
    """Run a command, return (rc, stdout, stderr). Never raises on rc != 0
    unless check=True."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        if check:
            raise
        return 127, "", "command not found: %s" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout: %s" % " ".join(argv)
    if check and p.returncode != 0:
        raise RuntimeError("command failed (%d): %s\n%s" % (p.returncode, " ".join(argv), p.stderr))
    return p.returncode, p.stdout, p.stderr


def which(name):
    from shutil import which as _which
    return _which(name)


def setup_logging(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    try:
        fh = logging.FileHandler(os.path.join(state_dir(), "arp.log"))
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError:
        pass
