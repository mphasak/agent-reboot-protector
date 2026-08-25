"""Snapshot daemon.

Two triggers:
  * periodic — every `periodic_minutes` a fresh catalog is taken, so even a
    surprise reboot loses at most a few minutes of layout drift;
  * scheduled shutdown — on Linux, systemd-logind publishes scheduled
    shutdowns/reboots (`shutdown -r +30`, many update managers); when one is
    within `shutdown_lead_minutes`, a snapshot is taken immediately and then
    refreshed every poll until the reboot happens.
"""

import os
import time

from .catalog import run_catalog
from .util import is_macos, log, run_cmd


def scheduled_shutdown_epoch():
    """Epoch seconds of a scheduled shutdown/reboot, or None."""
    if is_macos():
        return _macos_scheduled_shutdown()
    # systemd writes this file when a shutdown is scheduled.
    path = "/run/systemd/shutdown/scheduled"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = dict(line.strip().split("=", 1)
                        for line in f if "=" in line)
        usec = int(data.get("USEC", "0"))
        if usec:
            return usec / 1e6
    except (OSError, ValueError):
        pass
    # Fallback: ask logind over the bus.
    rc, out, _ = run_cmd(
        ["busctl", "get-property", "org.freedesktop.login1",
         "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
         "ScheduledShutdown"], timeout=5)
    if rc == 0:
        # Format: (st) "reboot" 1734567890000000
        parts = out.split()
        if len(parts) >= 3 and parts[1].strip('"'):
            try:
                usec = int(parts[2])
                if usec:
                    return usec / 1e6
            except ValueError:
                pass
    return None


def _macos_scheduled_shutdown():
    # `pmset -g sched` lists scheduled restart/shutdown events.
    rc, out, _ = run_cmd(["pmset", "-g", "sched"], timeout=5)
    if rc != 0:
        return None
    for line in out.splitlines():
        low = line.lower()
        if "restart" in low or "shutdown" in low:
            # e.g. "  [0]  restart at 10/12/2025 03:00:00"
            if " at " in line:
                stamp = line.split(" at ", 1)[1].strip()
                for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%y %H:%M:%S"):
                    try:
                        return time.mktime(time.strptime(stamp, fmt))
                    except ValueError:
                        continue
    return None


def run_watch(config, once=False):
    poll = config.get("watch_poll_seconds") or 30
    periodic = (config.get("periodic_minutes") or 5) * 60
    lead = (config.get("shutdown_lead_minutes") or 20) * 60
    last_catalog = 0.0
    log.info("watch loop started (periodic %ss, shutdown lead %ss)", periodic, lead)
    while True:
        now = time.time()
        reason = None
        sched = None
        try:
            sched = scheduled_shutdown_epoch()
        except Exception as e:
            log.debug("shutdown check failed: %s", e)
        if sched and 0 <= sched - now <= lead:
            # Refresh right up until the reboot, but no more than once a minute.
            if now - last_catalog >= 60:
                reason = "scheduled shutdown at %s" % time.strftime(
                    "%H:%M:%S", time.localtime(sched))
        if reason is None and now - last_catalog >= periodic:
            reason = "periodic"
        if reason:
            log.info("taking snapshot (%s)", reason)
            try:
                run_catalog(config, protect_nonempty=True)
                last_catalog = time.time()
            except Exception as e:
                log.error("catalog failed: %s", e)
        if once:
            return 0
        time.sleep(poll)
