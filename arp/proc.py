"""Process inspection: process table, ancestry, working dirs, open files.

Linux uses /proc directly; macOS shells out to ps/lsof.
"""

import os
import re
import sys
import time
from dataclasses import dataclass, field

from .util import run_cmd


@dataclass
class ProcInfo:
    pid: int
    ppid: int
    comm: str
    cmdline: list = field(default_factory=list)
    start_epoch: float = 0.0
    cwd: str = None


def _linux_btime():
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except OSError:
        pass
    return time.time() - _linux_uptime()


def _linux_uptime():
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            return float(f.read().split()[0])
    except OSError:
        return 0.0


def _linux_process_table():
    table = {}
    btime = _linux_btime()
    clk = os.sysconf("SC_CLK_TCK") or 100
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open("/proc/%d/stat" % pid, "r", encoding="utf-8", errors="replace") as f:
                stat = f.read()
            # comm may contain spaces/parens; it is bracketed by the outermost ().
            lp = stat.index("(")
            rp = stat.rindex(")")
            comm = stat[lp + 1:rp]
            fields = stat[rp + 2:].split()
            ppid = int(fields[1])           # field 4 overall
            starttime = float(fields[19])   # field 22 overall
            start_epoch = btime + starttime / clk
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                raw = f.read()
            cmdline = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
            try:
                cwd = os.readlink("/proc/%d/cwd" % pid)
            except OSError:
                cwd = None
            table[pid] = ProcInfo(pid, ppid, comm, cmdline, start_epoch, cwd)
        except (OSError, ValueError, IndexError):
            continue
    return table


_ETIME_RE = re.compile(r"^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$")


def _parse_etime(s):
    m = _ETIME_RE.match(s.strip())
    if not m:
        return 0
    days, hours, mins, secs = m.groups()
    total = int(secs) + 60 * int(mins)
    if hours:
        total += 3600 * int(hours)
    if days:
        total += 86400 * int(days)
    return total


def _macos_process_table():
    table = {}
    now = time.time()
    rc, out, _ = run_cmd(["ps", "-axo", "pid=,ppid=,etime=,comm="], timeout=15)
    if rc != 0:
        return table
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        start_epoch = now - _parse_etime(parts[2])
        table[pid] = ProcInfo(pid, ppid, parts[3].strip(), [], start_epoch, None)
    # Full argv in a second pass (comm= can be truncated / path-only).
    rc, out, _ = run_cmd(["ps", "-axo", "pid=,command="], timeout=15)
    if rc == 0:
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                pid = int(parts[0])
                if pid in table:
                    table[pid].cmdline = parts[1].split()
    return table


def process_table():
    if sys.platform == "darwin":
        return _macos_process_table()
    return _linux_process_table()


def children_map(table):
    kids = {}
    for p in table.values():
        kids.setdefault(p.ppid, []).append(p)
    for lst in kids.values():
        lst.sort(key=lambda p: p.pid)
    return kids


def descendants(table, root_pid, kids=None):
    """All descendants of root_pid in BFS order (shallowest first)."""
    if kids is None:
        kids = children_map(table)
    out = []
    queue = list(kids.get(root_pid, []))
    while queue:
        p = queue.pop(0)
        out.append(p)
        queue.extend(kids.get(p.pid, []))
    return out


def proc_cwd(pid):
    if sys.platform == "darwin":
        rc, out, _ = run_cmd(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"], timeout=15)
        if rc == 0:
            for line in out.splitlines():
                if line.startswith("n"):
                    return line[1:]
        return None
    try:
        return os.readlink("/proc/%d/cwd" % pid)
    except OSError:
        return None


def open_files(pid):
    """Paths of regular files the process holds open (best effort)."""
    paths = []
    if sys.platform == "darwin":
        rc, out, _ = run_cmd(["lsof", "-p", str(pid), "-Fn"], timeout=20)
        if rc == 0:
            for line in out.splitlines():
                if line.startswith("n/"):
                    paths.append(line[1:])
        return paths
    fd_dir = "/proc/%d/fd" % pid
    try:
        for fd in os.listdir(fd_dir):
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target.startswith("/"):
                paths.append(target)
    except OSError:
        pass
    return paths
