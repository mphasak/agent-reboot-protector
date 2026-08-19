"""Fallback backend for environments where windows cannot be enumerated
(e.g. GNOME on Wayland). Sessions are still cataloged from the process tree;
desktop placement is unavailable."""

import subprocess

from ..util import log
from .base import Backend


class GenericBackend(Backend):
    name = "generic"
    supports_placement = False

    def available(self):
        return True

    def ready(self):
        return True

    def list_windows(self):
        return []

    def move(self, window_id, desktop):
        log.warning("generic backend cannot move windows")

    def spawn(self, argv, desktop, cwd):
        subprocess.Popen(argv, cwd=cwd or None, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "spawned"
