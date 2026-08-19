"""Build the pre-reboot manifest: which terminal window sits on which desktop,
what folder it's in, which agent runs inside it, which session id that agent
is on, and a context snapshot for each session."""

import os
import re
import shutil
import time

from . import agents
from .proc import children_map, descendants, open_files, process_table
from .util import (atomic_write_json, boot_id, contexts_dir, history_dir, log,
                   manifest_path, now_iso)
from .wm import detect_backend

MANIFEST_VERSION = 1


def _terminal_pids(table, terminal):
    term = terminal.lower()
    pids = set()
    for p in table.values():
        base = os.path.basename(p.cmdline[0]).lower() if p.cmdline else p.comm.lower()
        # comm match too: nix wrappers show up as ".ghostty-wrapped".
        if term in base or term in p.comm.lower():
            pids.add(p.pid)
    return pids


def _window_roots(table, kids, term_pid):
    """Direct children of a terminal process = one per surface (window/tab)."""
    roots = []
    for child in kids.get(term_pid, []):
        # Skip obvious helper processes of the terminal itself.
        base = os.path.basename(child.cmdline[0]).lower() if child.cmdline else child.comm.lower()
        if base.startswith("ghostty") or base in ("io.elementary.", "dbus-launch"):
            continue
        roots.append(child)
    return roots


def _find_agent(table, kids, root):
    """Shallowest descendant (including the root) that is a known agent."""
    for p in [root] + descendants(table, root.pid, kids):
        kind = agents.classify(p.cmdline)
        if kind:
            return p, kind
    return None, None


def _shorten_home(path):
    home = os.path.expanduser("~")
    if path and path.startswith(home):
        return "~" + path[len(home):]
    return path or ""


def _match_score(window_title, root_info):
    """Heuristic for pairing a window with a shell tree when one terminal
    process owns several windows (single-instance mode)."""
    title = (window_title or "").lower()
    score = 0
    cwd = root_info.get("cwd") or ""
    if cwd:
        if _shorten_home(cwd).lower() in title or cwd.lower() in title:
            score += 3
        base = os.path.basename(cwd.rstrip("/")).lower()
        if base and base in title:
            score += 2
    kind = root_info.get("agent_kind") or ""
    for marker, pts in (("claude", 2), ("copilot", 2), ("cursor", 2)):
        if marker in title and marker in kind:
            score += pts
    return score


def _pair_windows(windows, roots_info):
    """Assign windows to root shells. Returns list of (window|None, root)."""
    if len(windows) == 1 and len(roots_info) == 1:
        return [(windows[0], roots_info[0])]
    pairs = []
    remaining = list(windows)
    scored = []
    for r in roots_info:
        for w in windows:
            scored.append((_match_score(w.title, r), w, id(r), r))
    scored.sort(key=lambda t: -t[0])
    used_w, used_r = set(), set()
    for score, w, rid, r in scored:
        if w.id in used_w or rid in used_r:
            continue
        used_w.add(w.id)
        used_r.add(rid)
        pairs.append((w, r, score))
    matched_r = {id(r) for _, r, _ in pairs}
    result = [(w, r) for w, r, _ in pairs]
    for r in roots_info:
        if id(r) not in matched_r:
            result.append((None, r))
    ambiguous = [p for p in pairs if p[2] == 0 and len(windows) > 1]
    if ambiguous:
        log.warning("window/shell pairing ambiguous for %d window(s); "
                    "desktop assignment may be approximate", len(ambiguous))
    return result


def _slug(s, maxlen=48):
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s or "").strip("-")
    return s[:maxlen] or "x"


def _write_context_file(ts_slug, entry, ctx):
    d = os.path.join(contexts_dir(), ts_slug)
    os.makedirs(d, exist_ok=True)
    sid = entry.get("session_id") or "unknown"
    name = "desktop-%s-%s-%s.md" % (
        entry.get("desktop", "x"), entry.get("kind", "shell"), _slug(sid)[:8])
    path = os.path.join(d, name)
    lines = [
        "# Agent session snapshot",
        "",
        "- captured: %s" % now_iso(),
        "- desktop: %s" % entry.get("desktop"),
        "- folder: %s" % entry.get("cwd"),
        "- agent: %s" % entry.get("kind"),
        "- session id: %s (confidence: %s)" % (sid, entry.get("session_confidence")),
        "- original command: `%s`" % " ".join(entry.get("agent_cmdline") or []),
    ]
    if ctx:
        if ctx.get("git_branch"):
            lines.append("- git branch: %s" % ctx["git_branch"])
        if ctx.get("summary"):
            lines += ["", "## Session summary", "", str(ctx["summary"])]
        if ctx.get("todos"):
            lines += ["", "## Task list (next steps)", ""]
            for t in ctx["todos"]:
                mark = "x" if t.get("status") == "completed" else " "
                lines.append("- [%s] %s" % (mark, t.get("content")))
        if ctx.get("last_user"):
            lines += ["", "## Last user message", "", str(ctx["last_user"])[:4000]]
        if ctx.get("last_assistant"):
            lines += ["", "## Last assistant message", "",
                      str(ctx["last_assistant"])[:4000]]
        if ctx.get("state_path"):
            lines += ["", "## Raw session state", "", "`%s`" % ctx["state_path"]]
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path
    except OSError as e:
        log.warning("could not write context file %s: %s", path, e)
        return None


def run_catalog(config, protect_nonempty=False):
    """Snapshot now. With protect_nonempty (used by the watch daemon), an
    empty result never replaces a non-empty manifest from this boot — guards
    against the WM tearing windows down mid-shutdown before our last pass."""
    started = time.time()
    terminal = config.get("terminal") or "ghostty"
    backend = detect_backend(config)
    table = process_table()
    kids = children_map(table)
    term_pids = _terminal_pids(table, terminal)
    if not term_pids:
        log.info("no %s processes found; writing empty manifest", terminal)

    try:
        windows = backend.list_windows()
    except Exception as e:
        log.warning("window enumeration failed (%s); falling back to process-only", e)
        windows = []
    windows_by_pid = {}
    for w in windows:
        windows_by_pid.setdefault(w.pid, []).append(w)

    active_window_s = (config.get("active_session_window_minutes") or 360) * 60
    ts_slug = time.strftime("%Y%m%d-%H%M%S")
    entries = []
    claimed_sessions = set()  # (kind, session_id) — unique per snapshot

    # Gather all root/agent info first so confident session matches claim ids
    # before ambiguous ones.
    prepared = []
    for tpid in sorted(term_pids):
        roots = _window_roots(table, kids, tpid)
        roots_info = []
        for root in roots:
            agent_proc, kind = _find_agent(table, kids, root)
            cwd = (agent_proc.cwd if agent_proc and agent_proc.cwd else root.cwd)
            if cwd is None and agent_proc:
                from .proc import proc_cwd
                cwd = proc_cwd(agent_proc.pid)
            roots_info.append({
                "root": root, "agent": agent_proc, "agent_kind": kind, "cwd": cwd,
            })
        wins = windows_by_pid.get(tpid, [])
        for w, r in _pair_windows(wins, roots_info):
            prepared.append((w, r))

    # Terminals whose windows we saw but whose pid had no shell children
    # (shouldn't normally happen) are ignored; process-only roots keep
    # desktop=None.
    def _cands_for(item):
        w, r = item
        if not r["agent_kind"]:
            return []
        mod = agents.get(r["agent_kind"])
        paths = open_files(r["agent"].pid)
        return mod.session_candidates(r["agent"], paths, r["cwd"], active_window_s)

    with_cands = [(item, _cands_for(item)) for item in prepared]
    # Most-confident first so they claim session ids first.
    with_cands.sort(key=lambda t: t[1][0].tier if t[1] else 99)

    for item, cands in with_cands:
        w, r = item
        entry = {
            "desktop": w.desktop if w else None,
            "window_id": w.id if w else None,
            "window_title": w.title if w else None,
            "terminal_pid": r["root"].ppid,
            "shell_pid": r["root"].pid,
            "cwd": r["cwd"],
            "kind": r["agent_kind"],
            "agent_pid": r["agent"].pid if r["agent"] else None,
            "agent_cmdline": r["agent"].cmdline if r["agent"] else None,
            "session_id": None,
            "session_confidence": "none",
            "session_path": None,
            "context_file": None,
        }
        chosen = None
        for c in cands:
            if (entry["kind"], c.session_id) not in claimed_sessions:
                chosen = c
                break
        if chosen:
            claimed_sessions.add((entry["kind"], chosen.session_id))
            entry["session_id"] = chosen.session_id
            entry["session_confidence"] = chosen.confidence
            entry["session_path"] = chosen.path
            mod = agents.get(entry["kind"])
            try:
                ctx = mod.extract_context(chosen.path, chosen.session_id)
            except Exception as e:
                log.warning("context extraction failed for %s: %s", chosen.session_id, e)
                ctx = None
            entry["context_file"] = _write_context_file(ts_slug, entry, ctx)
        elif entry["kind"]:
            log.warning("no session found for %s in %s (pid %s)",
                        entry["kind"], entry["cwd"], entry["agent_pid"])
        entries.append(entry)

    # Stable order: by desktop, then cwd.
    entries.sort(key=lambda e: (str(e["desktop"]) if e["desktop"] is not None else "zzz",
                                e["cwd"] or ""))

    if protect_nonempty and not entries:
        from .util import read_json
        prev = read_json(manifest_path())
        if prev and prev.get("entries") and prev.get("boot_id") == boot_id():
            log.warning("snapshot found no windows but the current manifest has "
                        "%d entries from this boot — keeping the old manifest",
                        len(prev["entries"]))
            return prev

    manifest = {
        "version": MANIFEST_VERSION,
        "created_at": now_iso(),
        "created_epoch": time.time(),
        "boot_id": boot_id(),
        "backend": backend.name,
        "terminal": terminal,
        "consumed": False,
        "entries": entries,
    }
    atomic_write_json(manifest_path(), manifest)
    # Keep a history copy.
    hist = os.path.join(history_dir(), "manifest-%s.json" % ts_slug)
    try:
        shutil.copyfile(manifest_path(), hist)
        _prune_history(config.get("history_keep") or 30)
    except OSError:
        pass

    agents_found = sum(1 for e in entries if e["kind"])
    with_session = sum(1 for e in entries if e["session_id"])
    log.info("cataloged %d window(s): %d agent(s), %d with session ids (%.1fs)",
             len(entries), agents_found, with_session, time.time() - started)
    return manifest


def _prune_history(keep):
    d = history_dir()
    try:
        files = sorted(os.listdir(d))
    except OSError:
        files = []
    for name in files[:-keep] if len(files) > keep else []:
        try:
            os.unlink(os.path.join(d, name))
        except OSError:
            pass
    # Contexts are grouped in per-snapshot directories; prune those too.
    cdir = contexts_dir()
    try:
        snaps = sorted(os.listdir(cdir))
    except OSError:
        return
    for name in snaps[:-keep] if len(snaps) > keep else []:
        shutil.rmtree(os.path.join(cdir, name), ignore_errors=True)
