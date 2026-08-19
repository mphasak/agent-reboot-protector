"""Window-manager backends and autodetection.

A backend knows how to enumerate terminal windows with their virtual-desktop
placement, move windows between desktops, and spawn a new terminal window on a
given desktop.
"""

import os

from ..util import is_macos, log, which
from .base import Backend, Window  # noqa: F401  (re-exported)


def detect_backend(config):
    forced = (config or {}).get("backend")
    order = ["hyprland", "sway", "x11", "macos", "generic"]
    if forced:
        order = [forced]
    for name in order:
        b = _make(name, config)
        if b is not None and b.available():
            log.debug("window backend: %s", b.name)
            return b
    from .generic import GenericBackend
    return GenericBackend(config)


def _make(name, config):
    if name == "hyprland":
        from .hyprland import HyprlandBackend
        return HyprlandBackend(config)
    if name == "sway":
        from .sway import SwayBackend
        return SwayBackend(config)
    if name == "x11":
        from .x11 import X11Backend
        return X11Backend(config)
    if name == "macos":
        from .macos import MacOSBackend
        return MacOSBackend(config)
    if name == "generic":
        from .generic import GenericBackend
        return GenericBackend(config)
    log.warning("unknown backend %r", name)
    return None
