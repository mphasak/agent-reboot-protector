"""Helpers shared by agent adapters."""

import json
import os
import re
import time

UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class Candidate:
    """A possible session for a running agent process, ranked by tier
    (lower = more confident) then recency."""

    def __init__(self, session_id, tier, confidence, path=None, mtime=0.0):
        self.session_id = session_id
        self.tier = tier
        self.confidence = confidence
        self.path = path
        self.mtime = mtime

    def sort_key(self):
        return (self.tier, -self.mtime)

    def __repr__(self):
        return "Candidate(%s, %s)" % (self.session_id, self.confidence)


def fd_candidates(open_paths, root_marker, id_from_path):
    """Tier-0 candidates: session files the process actually holds open."""
    out = []
    for p in open_paths:
        if root_marker not in p:
            continue
        sid = id_from_path(p)
        if sid:
            mtime = _safe_mtime(p)
            out.append(Candidate(sid, 0, "open-file", p, mtime))
    return out


def _safe_mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def mtime_candidates(paths_with_ids, active_window_s, now=None):
    """Tier candidates from session files/dirs by modification recency.

    paths_with_ids: iterable of (path, session_id).
    Active files (touched within active_window_s) rank tier 2; stale tier 3.
    """
    now = now or time.time()
    out = []
    for path, sid in paths_with_ids:
        mtime = _safe_mtime(path)
        if mtime <= 0:
            continue
        if now - mtime <= active_window_s:
            out.append(Candidate(sid, 2, "active-mtime", path, mtime))
        else:
            out.append(Candidate(sid, 3, "stale-mtime", path, mtime))
    return out


def flag_passthrough(cmdline, bool_flags, value_flags):
    """Extract flags worth carrying over into a resume command.

    bool_flags: flags with no value; value_flags: flags followed by a value
    (either "--flag value" or "--flag=value")."""
    out = []
    i = 0
    args = list(cmdline[1:]) if cmdline else []
    while i < len(args):
        a = args[i]
        name = a.split("=", 1)[0]
        if name in bool_flags and "=" not in a:
            out.append(a)
        elif name in value_flags:
            if "=" in a:
                out.append(a)
            elif i + 1 < len(args):
                out.extend([a, args[i + 1]])
                i += 1
        i += 1
    return out


def tail_lines(path, max_bytes=262144):
    """Last lines of a file, reading at most max_bytes from the end."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # drop the (probably partial) first line
            data = f.read()
        return data.decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def parse_json_lines(lines):
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def latest_in_dir(d, pattern_suffix=None):
    """(path, mtime) entries in a directory, newest first."""
    try:
        names = os.listdir(d)
    except OSError:
        return []
    out = []
    for n in names:
        if pattern_suffix and not n.endswith(pattern_suffix):
            continue
        p = os.path.join(d, n)
        out.append((p, _safe_mtime(p)))
    out.sort(key=lambda t: -t[1])
    return out
