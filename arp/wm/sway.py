"""Sway backend via swaymsg. Desktops are workspace names (strings)."""

import json
import os

from ..util import log, run_cmd, which
from .base import Backend, Window


class SwayBackend(Backend):
    name = "sway"

    def available(self):
        return bool(os.environ.get("SWAYSOCK")) and bool(which("swaymsg"))

    def ready(self):
        rc, _, _ = run_cmd(["swaymsg", "-t", "get_version"], timeout=5)
        return rc == 0

    def list_windows(self):
        rc, out, err = run_cmd(["swaymsg", "-t", "get_tree"], timeout=10)
        if rc != 0:
            log.warning("swaymsg get_tree failed: %s", err.strip())
            return []
        try:
            tree = json.loads(out)
        except ValueError:
            return []
        windows = []

        def walk(node, workspace):
            if node.get("type") == "workspace":
                workspace = node.get("name")
            app = node.get("app_id") or (node.get("window_properties") or {}).get("class") or ""
            if node.get("pid") and self._terminal_matches(app):
                windows.append(Window(
                    str(node["id"]), workspace, int(node.get("pid") or 0),
                    node.get("name") or ""))
            for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []):
                walk(child, workspace)

        walk(tree, None)
        return windows

    def ensure_desktops(self, needed):
        # Sway workspaces exist on demand.
        pass

    def move(self, window_id, desktop):
        rc, _, err = run_cmd(
            ["swaymsg", "[con_id=%s] move container to workspace %s" % (window_id, desktop)],
            timeout=5)
        if rc != 0:
            log.warning("failed to move %s to workspace %s: %s", window_id, desktop, err.strip())
