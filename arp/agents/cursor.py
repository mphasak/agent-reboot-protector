"""Cursor CLI adapter (`agent`, formerly and still aliased `cursor-agent`).

Chats live in ~/.cursor/chats/<md5(absolute-workspace-path)>/<chat-id>/store.db
(plain lowercase-hex md5 of the path string). Readable transcripts also land
under ~/.cursor/projects/<sanitized-path>/agent-transcripts/. Neither layout
is a stable API, so discovery stays multi-strategy.
"""

import hashlib
import os

from .common import (UUID_RE, fd_candidates, flag_passthrough, latest_in_dir,
                     mtime_candidates)

KIND = "cursor"
BINARY = "cursor-agent"  # alias kept for compatibility; new name is `agent`

_BOOL_FLAGS = {"-f", "--force", "--fullscreen"}
_VALUE_FLAGS = {"--model", "-m"}


def _chats_root():
    return os.path.expanduser("~/.cursor/chats")


def _sid_from_path(path):
    root = _chats_root()
    if path.startswith(root):
        rel = path[len(root):].strip("/")
        parts = rel.split("/")
        # chats/<workspace-md5>/<chat-id>/...
        if len(parts) >= 2:
            return parts[1]
    m = UUID_RE.search(path)
    return m.group(0) if m else None


def _workspace_dir(cwd):
    if not cwd:
        return None
    h = hashlib.md5(os.path.abspath(cwd).encode("utf-8")).hexdigest()
    p = os.path.join(_chats_root(), h)
    return p if os.path.isdir(p) else None


def session_candidates(proc, open_paths, cwd, active_window_s):
    cands = fd_candidates(open_paths, "/.cursor/", _sid_from_path)
    pairs = []
    wdir = _workspace_dir(cwd)
    if wdir:
        for path, _mt in latest_in_dir(wdir):
            pairs.append((path, os.path.basename(path)))
        scored = mtime_candidates(pairs, active_window_s)
        for c in scored:
            # The md5(cwd) bucket is per-workspace, so this is strong evidence.
            c.tier = min(c.tier, 1)
            if c.confidence == "active-mtime":
                c.confidence = "workspace-match"
        cands.extend(scored)
    else:
        for wpath, _mt in latest_in_dir(_chats_root()):
            for path, _mt2 in latest_in_dir(wpath):
                pairs.append((path, os.path.basename(path)))
        cands.extend(mtime_candidates(pairs, active_window_s))
    cands.sort(key=lambda c: c.sort_key())
    return cands


def _binary_from(original_cmdline):
    """Respect whichever entrypoint the user ran (`agent` or `cursor-agent`)."""
    for tok in (original_cmdline or [])[:3]:
        base = os.path.basename(tok)
        if base in ("agent", "cursor-agent"):
            return base
    return BINARY


def resume_argv(session_id, original_cmdline, config):
    extra = ((config.get("agents") or {}).get(KIND) or {}).get("extra_args") or []
    keep = flag_passthrough(original_cmdline or [], _BOOL_FLAGS, _VALUE_FLAGS)
    return [_binary_from(original_cmdline), "--resume=%s" % session_id] + keep + list(extra)


def fresh_argv(context_file, original_cmdline, config):
    extra = ((config.get("agents") or {}).get(KIND) or {}).get("extra_args") or []
    keep = flag_passthrough(original_cmdline or [], _BOOL_FLAGS, _VALUE_FLAGS)
    prompt = ("A machine reboot interrupted our previous session and it could "
              "not be resumed by id. Read %s for a snapshot of that session's "
              "context and next steps, then continue the work." % context_file)
    return [_binary_from(original_cmdline)] + keep + list(extra) + [prompt]


def extract_context(session_path, session_id):
    """Pull the session name out of store.db's meta table when possible."""
    info = {"state_path": session_path}
    db = os.path.join(session_path or "", "store.db")
    if not os.path.isfile(db):
        return info
    try:
        import json
        import sqlite3
        conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='0'").fetchone()
        finally:
            conn.close()
        if row and row[0]:
            raw = row[0]
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            # value is hex-encoded JSON in known versions
            try:
                decoded = bytes.fromhex(raw).decode("utf-8", "replace")
            except ValueError:
                decoded = raw
            meta = json.loads(decoded)
            if isinstance(meta, dict):
                info["summary"] = meta.get("name") or meta.get("title")
    except Exception:
        pass
    return info
