"""macOS backend.

Full desktop (Space) support requires yabai (https://github.com/koekeishiya/yabai)
because macOS has no public API to query or set a window's Space. Without
yabai this backend degrades: windows are cataloged without Space info and
restored without placement.
"""

import json
import subprocess
import sys
import time

from ..util import log, run_cmd, which
from .base import Backend, Window


class MacOSBackend(Backend):
    name = "macos"

    def __init__(self, config=None):
        super().__init__(config)
        self.yabai = bool(which("yabai"))
        self.supports_placement = self.yabai

    def available(self):
        return sys.platform == "darwin"

    def ready(self):
        if self.yabai:
            rc, _, _ = run_cmd(["yabai", "-m", "query", "--spaces"], timeout=5)
            return rc == 0
        # Consider the session ready once Finder responds.
        rc, _, _ = run_cmd(
            ["osascript", "-e", 'tell application "System Events" to count processes'],
            timeout=10)
        return rc == 0

    def _yabai_windows(self):
        rc, out, err = run_cmd(["yabai", "-m", "query", "--windows"], timeout=10)
        if rc != 0:
            log.warning("yabai query failed: %s", err.strip())
            return []
        try:
            return json.loads(out)
        except ValueError:
            return []

    def list_windows(self):
        term = (self.config.get("terminal") or "ghostty").lower()
        if self.yabai:
            windows = []
            for w in self._yabai_windows():
                if term not in (w.get("app") or "").lower():
                    continue
                windows.append(Window(
                    str(w.get("id")), w.get("space"), int(w.get("pid") or 0),
                    w.get("title") or ""))
            return windows
        # Degraded: titles only via System Events; no Space info.
        script = (
            'tell application "System Events" to tell (first process whose '
            'name contains "%s") to get name of windows' % term.capitalize())
        rc, out, _ = run_cmd(["osascript", "-e", script], timeout=10)
        if rc != 0:
            return []
        titles = [t.strip() for t in out.strip().split(",") if t.strip()]
        return [Window("osa-%d" % i, None, 0, t) for i, t in enumerate(titles)]

    def desktop_count(self):
        if not self.yabai:
            return None
        rc, out, _ = run_cmd(["yabai", "-m", "query", "--spaces"], timeout=5)
        if rc != 0:
            return None
        try:
            return len(json.loads(out))
        except ValueError:
            return None

    def ensure_desktops(self, needed):
        if not self.yabai:
            return
        count = self.desktop_count() or 0
        for _ in range(max(0, needed - count)):
            run_cmd(["yabai", "-m", "space", "--create"], timeout=5)

    def move(self, window_id, desktop):
        if not self.yabai:
            log.warning("cannot move windows between Spaces without yabai")
            return
        rc, _, err = run_cmd(
            ["yabai", "-m", "window", str(window_id), "--space", str(desktop)], timeout=5)
        if rc != 0:
            log.warning("failed to move window %s to space %s: %s",
                        window_id, desktop, err.strip())

    def spawn(self, argv, desktop, cwd):
        # `open -na Ghostty --args ...` starts a new window even when the app
        # is already running. argv[0] is the terminal binary name; pass the
        # rest through --args.
        before = {w.id for w in self.list_windows()} if self.yabai else set()
        app = self.config.get("terminal_app") or "Ghostty"
        cmd = ["open", "-na", app, "--args"] + list(argv[1:])
        subprocess.Popen(cmd, cwd=cwd or None,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not self.yabai:
            return None
        timeout = (self.config.get("restore") or {}).get("spawn_window_timeout_seconds", 15)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.25)
            for w in self.list_windows():
                if w.id not in before:
                    if desktop is not None:
                        self.move(w.id, desktop)
                    return w.id
        log.warning("spawned terminal but no new window appeared within %ss", timeout)
        return None
