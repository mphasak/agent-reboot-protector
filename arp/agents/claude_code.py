"""Claude Code adapter.

Sessions live in ~/.claude/projects/<munged-cwd>/<session-uuid>.jsonl where the
munge replaces non-alphanumeric characters with "-". Resuming with
`claude --resume <id>` appends to the same file (same session id), so the id
we catalog before a reboot stays valid after it.
"""

import glob
import json
import os
import re

from .common import (Candidate, UUID_RE, fd_candidates, flag_passthrough,
                     latest_in_dir, mtime_candidates, parse_json_lines,
                     tail_lines)

KIND = "claude-code"
BINARY = "claude"

_BOOL_FLAGS = {"--dangerously-skip-permissions", "--verbose", "--continue", "-c"}
_VALUE_FLAGS = {"--model", "--permission-mode", "--add-dir", "--settings",
                "--allowedTools", "--allowed-tools", "--append-system-prompt"}
# --continue/-c must not survive into a --resume command.
_STRIP_FROM_RESUME = {"--continue", "-c"}


def _claude_home():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def _project_dirs(cwd):
    """Candidate project dirs for a working directory.

    The exact munging rule is version-dependent, so try the two known
    variants and fall back to any project dir whose name matches after
    normalizing both sides.
    """
    root = os.path.join(_claude_home(), "projects")
    if not cwd or not os.path.isdir(root):
        return []
    variants = {
        re.sub(r"[^A-Za-z0-9]", "-", cwd),
        re.sub(r"[/.]", "-", cwd),
    }
    dirs = []
    for v in variants:
        p = os.path.join(root, v)
        if os.path.isdir(p) and p not in dirs:
            dirs.append(p)
    if not dirs:
        # Normalized comparison against every project dir.
        want = re.sub(r"[^a-z0-9]", "", cwd.lower())
        try:
            for name in os.listdir(root):
                if re.sub(r"[^a-z0-9]", "", name.lower()) == want:
                    dirs.append(os.path.join(root, name))
        except OSError:
            pass
    return dirs


def _sid_from_path(path):
    base = os.path.basename(path)
    if not base.endswith(".jsonl"):
        return None
    m = UUID_RE.search(base)
    return m.group(0) if m else None


def session_candidates(proc, open_paths, cwd, active_window_s):
    """Ranked Candidate list for a running claude process."""
    cands = fd_candidates(open_paths, "/projects/", _sid_from_path)
    # fd matching may hit unrelated jsonl files; keep only ones under the
    # claude home.
    home = _claude_home()
    cands = [c for c in cands if c.path and c.path.startswith(home)]

    pairs = []
    for d in _project_dirs(cwd):
        for path, _mt in latest_in_dir(d, ".jsonl"):
            sid = _sid_from_path(path)
            if sid:
                pairs.append((path, sid))
    for c in mtime_candidates(pairs, active_window_s):
        # Tier 1 boost: session start close to process start (fresh session),
        # helps disambiguate multiple sessions in one folder.
        if proc and proc.start_epoch and c.path:
            first_ts = _first_entry_epoch(c.path)
            if first_ts and abs(first_ts - proc.start_epoch) < 180:
                c.tier = 1
                c.confidence = "start-time-match"
        cands.append(c)
    cands.sort(key=lambda c: c.sort_key())
    return cands


def _first_entry_epoch(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
        obj = json.loads(first)
        ts = obj.get("timestamp")
        if isinstance(ts, str):
            import datetime
            return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (OSError, ValueError, TypeError):
        pass
    return None


def resume_argv(session_id, original_cmdline, config):
    extra = ((config.get("agents") or {}).get(KIND) or {}).get("extra_args") or []
    keep = flag_passthrough(original_cmdline or [], _BOOL_FLAGS, _VALUE_FLAGS)
    keep = [a for a in keep if a.split("=", 1)[0] not in _STRIP_FROM_RESUME]
    return [BINARY, "--resume", session_id] + keep + list(extra)


def fresh_argv(context_file, original_cmdline, config):
    extra = ((config.get("agents") or {}).get(KIND) or {}).get("extra_args") or []
    keep = flag_passthrough(original_cmdline or [], _BOOL_FLAGS, _VALUE_FLAGS)
    keep = [a for a in keep if a.split("=", 1)[0] not in _STRIP_FROM_RESUME]
    prompt = ("A machine reboot interrupted our previous session and it could "
              "not be resumed by id. Read %s for a snapshot of that session's "
              "context and next steps, then continue the work." % context_file)
    return [BINARY] + keep + list(extra) + [prompt]


def extract_context(session_path, session_id):
    """Best-effort context snapshot from the session transcript.

    The .jsonl format is internal to Claude Code and unstable, so everything
    here is defensive: missing fields simply produce a sparser snapshot.
    """
    info = {"last_user": None, "last_assistant": None, "summary": None,
            "todos": None, "cwd": None, "git_branch": None}
    entries = parse_json_lines(tail_lines(session_path))
    for obj in reversed(entries):
        t = obj.get("type")
        msg = obj.get("message") or {}
        if info["cwd"] is None and obj.get("cwd"):
            info["cwd"] = obj["cwd"]
        if info["git_branch"] is None and obj.get("gitBranch"):
            info["git_branch"] = obj["gitBranch"]
        if t == "assistant" and info["last_assistant"] is None:
            info["last_assistant"] = _text_of(msg.get("content"))
        elif t == "user" and info["last_user"] is None:
            text = _text_of(msg.get("content"))
            if text:  # skip pure tool_result turns
                info["last_user"] = text
        elif t == "summary" and info["summary"] is None:
            info["summary"] = obj.get("summary")
        if info["last_user"] and info["last_assistant"]:
            break
    info["todos"] = _todos_for(session_id)
    return info


def _text_of(content):
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        text = "\n".join(p for p in parts if p).strip()
        return text or None
    return None


def _todos_for(session_id):
    todo_dir = os.path.join(_claude_home(), "todos")
    matches = sorted(glob.glob(os.path.join(todo_dir, "*%s*.json" % session_id)),
                     key=lambda p: -os.path.getmtime(p) if os.path.exists(p) else 0)
    for path in matches:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        items = data if isinstance(data, list) else data.get("todos")
        if isinstance(items, list) and items:
            out = []
            for it in items:
                if isinstance(it, dict):
                    out.append({"status": it.get("status"),
                                "content": it.get("content")})
            if out:
                return out
    return None
