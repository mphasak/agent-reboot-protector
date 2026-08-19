"""Hyprland backend via hyprctl. Desktops are Hyprland workspace ids (ints)."""

import json
import os
import shlex

from ..util import log, run_cmd, which
from .base import Backend, Window


class HyprlandBackend(Backend):
    name = "hyprland"

    def available(self):
        return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")) and bool(which("hyprctl"))

    def ready(self):
        rc, _, _ = run_cmd(["hyprctl", "-j", "monitors"], timeout=5)
        return rc == 0

    def _clients(self):
        rc, out, err = run_cmd(["hyprctl", "-j", "clients"], timeout=10)
        if rc != 0:
            log.warning("hyprctl clients failed: %s", err.strip())
            return []
        try:
            return json.loads(out)
        except ValueError:
            return []

    def list_windows(self):
        windows = []
        for c in self._clients():
            cls = c.get("class") or c.get("initialClass") or ""
            if not self._terminal_matches(cls):
                continue
            ws = (c.get("workspace") or {}).get("id")
            windows.append(Window(
                c.get("address"), ws, int(c.get("pid") or 0), c.get("title") or ""))
        return windows

    def ensure_desktops(self, needed):
        # Hyprland workspaces are created on demand.
        pass

    def move(self, window_id, desktop):
        rc, _, err = run_cmd(
            ["hyprctl", "dispatch", "movetoworkspacesilent",
             "%s,address:%s" % (desktop, window_id)], timeout=5)
        if rc != 0:
            log.warning("failed to move %s to workspace %s: %s", window_id, desktop, err.strip())

    def spawn(self, argv, desktop, cwd):
        cmd = " ".join(shlex.quote(a) for a in argv)
        if cwd:
            cmd = "sh -c %s" % shlex.quote("cd %s && exec %s" % (shlex.quote(cwd), cmd))
        if desktop is not None:
            dispatch = "[workspace %s silent] %s" % (desktop, cmd)
        else:
            dispatch = cmd
        rc, _, err = run_cmd(["hyprctl", "dispatch", "exec", dispatch], timeout=10)
        if rc != 0:
            log.warning("hyprctl exec failed: %s", err.strip())
            return None
        return "spawned"
