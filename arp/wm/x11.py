"""X11 backend via wmctrl (EWMH). Desktops are 0-based ints."""

import os

from ..util import log, run_cmd, which
from .base import Backend, Window


class X11Backend(Backend):
    name = "x11"

    def available(self):
        if not os.environ.get("DISPLAY"):
            return False
        if not which("wmctrl"):
            return False
        # Under native Wayland compositors, prefer their own backends
        # (they are checked before this one in detection order).
        rc, _, _ = run_cmd(["wmctrl", "-m"], timeout=5)
        return rc == 0

    def ready(self):
        rc, _, _ = run_cmd(["wmctrl", "-d"], timeout=5)
        return rc == 0

    def list_windows(self):
        # -l list, -p pids, -x WM_CLASS
        rc, out, err = run_cmd(["wmctrl", "-lpx"], timeout=10)
        if rc != 0:
            log.warning("wmctrl -lpx failed: %s", err.strip())
            return []
        windows = []
        for line in out.splitlines():
            # 0x04000007  3 12345  ghostty.com.mitchellh.ghostty  host  title...
            parts = line.split(None, 5)
            if len(parts) < 5:
                continue
            wid, desktop, pid, wmclass = parts[0], parts[1], parts[2], parts[3]
            title = parts[5] if len(parts) > 5 else ""
            if not self._terminal_matches(wmclass):
                continue
            try:
                windows.append(Window(wid, int(desktop), int(pid), title))
            except ValueError:
                continue
        return windows

    def desktop_count(self):
        rc, out, _ = run_cmd(["wmctrl", "-d"], timeout=5)
        if rc != 0:
            return None
        return len([l for l in out.splitlines() if l.strip()])

    def ensure_desktops(self, needed):
        count = self.desktop_count()
        if count is not None and needed > count:
            log.info("growing desktop count %d -> %d", count, needed)
            run_cmd(["wmctrl", "-n", str(needed)], timeout=5)

    def move(self, window_id, desktop):
        rc, _, err = run_cmd(
            ["wmctrl", "-i", "-r", str(window_id), "-t", str(int(desktop))], timeout=5)
        if rc != 0:
            log.warning("failed to move %s to desktop %s: %s", window_id, desktop, err.strip())
