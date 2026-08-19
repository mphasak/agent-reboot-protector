"""GitHub Copilot CLI adapter (the agentic `copilot` terminal CLI).

Current releases store sessions in ~/.copilot/session-state/<session-uuid>/
containing events.jsonl and workspace.yaml (which records the session's cwd).
Older releases used ~/.copilot/history-session-state/ or a flat
session-state/<id>.jsonl — all layouts are scanned. COPILOT_HOME relocates
the config dir.
"""

import os

from .common import (UUID_RE, fd_candidates, flag_passthrough, latest_in_dir,
                     mtime_candidates)

KIND = "copilot"
BINARY = "copilot"

_BOOL_FLAGS = {"--allow-all-tools", "--no-color", "--banner"}
_VALUE_FLAGS = {"--model", "--allow-tool", "--deny-tool", "--add-dir",
                "--log-level"}


def _copilot_home():
    return os.environ.get("COPILOT_HOME") or os.path.expanduser("~/.copilot")


def _state_roots():
    home = _copilot_home()
    roots = []
    for name in ("session-state", "history-session-state"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            roots.append(p)
    return roots


def _sid_from_path(path):
    m = UUID_RE.search(path)
    return m.group(0) if m else None


def _workspace_cwd(session_path):
    """The cwd recorded in a session dir's workspace.yaml (best effort;
    the file is small and flat, so a line scan beats a YAML dependency)."""
    if not os.path.isdir(session_path):
        return None
    ypath = os.path.join(session_path, "workspace.yaml")
    try:
        with open(ypath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("cwd:"):
                    val = line.split(":", 1)[1].strip().strip("'\"")
                    return val or None
    except OSError:
        pass
    return None


def _mentions_cwd(path, cwd):
    """Fallback for legacy layouts: does the state reference this directory?"""
    if not cwd:
        return False
    needle = cwd.encode("utf-8")
    files = [path] if os.path.isfile(path) else []
    if os.path.isdir(path):
        try:
            files = [os.path.join(path, n) for n in os.listdir(path)][:20]
        except OSError:
            return False
    for f in files:
        try:
            if not os.path.isfile(f) or os.path.getsize(f) > 8 * 1024 * 1024:
                continue
            with open(f, "rb") as fh:
                if needle in fh.read():
                    return True
        except OSError:
            continue
    return False


def session_candidates(proc, open_paths, cwd, active_window_s):
    cands = fd_candidates(open_paths, "/.copilot/", _sid_from_path)
    copilot_home = _copilot_home()
    cands = [c for c in cands if c.path and c.path.startswith(copilot_home)]
    pairs = []
    for root in _state_roots():
        for path, _mt in latest_in_dir(root):
            sid = _sid_from_path(path)
            if sid:
                pairs.append((path, sid))
    scored = mtime_candidates(pairs, active_window_s)
    for c in scored:
        state_cwd = _workspace_cwd(c.path) if c.path else None
        if state_cwd and cwd and os.path.abspath(state_cwd) == os.path.abspath(cwd):
            c.tier = min(c.tier, 1)
            c.confidence = "cwd-match"
        elif state_cwd and cwd:
            # Definitely some other workspace's session.
            c.tier = max(c.tier, 4)
        elif c.path and _mentions_cwd(c.path, cwd):
            c.tier = min(c.tier, 1)
            c.confidence = "cwd-match"
    cands.extend(scored)
    cands.sort(key=lambda c: c.sort_key())
    return cands


def resume_argv(session_id, original_cmdline, config):
    extra = ((config.get("agents") or {}).get(KIND) or {}).get("extra_args") or []
    keep = flag_passthrough(original_cmdline or [], _BOOL_FLAGS, _VALUE_FLAGS)
    return [BINARY, "--resume=%s" % session_id] + keep + list(extra)


def fresh_argv(context_file, original_cmdline, config):
    # `--continue` resumes the most recent session in the current working
    # directory — a reasonable fallback when no id was captured.
    extra = ((config.get("agents") or {}).get(KIND) or {}).get("extra_args") or []
    keep = flag_passthrough(original_cmdline or [], _BOOL_FLAGS, _VALUE_FLAGS)
    return [BINARY, "--continue"] + keep + list(extra)


def extract_context(session_path, session_id):
    """Snapshot workspace metadata and the tail of the event log."""
    info = {"state_path": session_path}
    if not session_path or not os.path.isdir(session_path):
        return info
    ypath = os.path.join(session_path, "workspace.yaml")
    try:
        with open(ypath, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                for key, out in (("summary:", "summary"), ("branch:", "git_branch"),
                                 ("cwd:", "cwd")):
                    if line.startswith(key):
                        info[out] = line.split(":", 1)[1].strip().strip("'\"") or None
    except OSError:
        pass
    return info
