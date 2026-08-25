"""Backend interface."""

import subprocess
import time
from dataclasses import dataclass

from ..util import log


@dataclass
class Window:
    id: str          # backend-native window identifier
    desktop: object  # backend-native desktop identifier (int or str), or None
    pid: int         # owning process pid (0 if unknown)
    title: str


class Backend:
    name = "base"
    # True when this backend can place windows on desktops.
    supports_placement = True

    def __init__(self, config=None):
        self.config = config or {}

    def available(self):
        raise NotImplementedError

    def ready(self):
        """Is the WM up and answering? Used to wait at login."""
        return self.available()

    def wait_ready(self, timeout_s):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                if self.ready():
                    return True
            except Exception as e:  # pragma: no cover - defensive
                log.debug("ready check failed: %s", e)
            time.sleep(2)
        return False

    def list_windows(self):
        """All terminal windows as Window objects. May be [] if none."""
        raise NotImplementedError

    def desktop_count(self):
        return None

    def ensure_desktops(self, needed):
        """Make sure at least `needed` desktops exist (no-op where dynamic)."""

    def move(self, window_id, desktop):
        raise NotImplementedError

    def _terminal_matches(self, s):
        term = (self.config.get("terminal") or "ghostty").lower()
        return term in (s or "").lower()

    def spawn(self, argv, desktop, cwd):
        """Spawn a terminal window running argv, place it on desktop.

        Default implementation: diff the window list before/after so it works
        even when the terminal runs single-instance (new windows belong to an
        existing process). Returns the new window id or None.
        """
        before = {w.id for w in self.list_windows()}
        subprocess.Popen(argv, cwd=cwd or None, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        timeout = (self.config.get("restore") or {}).get("spawn_window_timeout_seconds", 15)
        deadline = time.time() + timeout
        new_id = None
        while time.time() < deadline:
            time.sleep(0.25)
            for w in self.list_windows():
                if w.id not in before:
                    new_id = w.id
                    break
            if new_id:
                break
        if new_id is None:
            log.warning("spawned terminal but no new window appeared within %ss", timeout)
            return None
        if desktop is not None and self.supports_placement:
            self.move(new_id, desktop)
        return new_id
