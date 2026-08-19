"""Rebuild the pre-reboot layout: one terminal window per manifest entry, on
the right desktop, in the right folder, resuming the right agent session."""

import os
import shlex
import time

from . import agents
from .util import (atomic_write_json, boot_id, log, manifest_path, read_json)
from .wm import detect_backend


def _wrap_shell(inner_cmd, banner, context_file, keep_shell_open):
    """Run the resume command inside the user's login shell so PATH and rc
    setup apply (agent CLIs commonly live in ~/.local/bin etc.)."""
    shell = os.environ.get("SHELL") or "/bin/sh"
    parts = []
    if banner:
        parts.append("echo %s" % shlex.quote(banner))
    parts.append(inner_cmd)
    if keep_shell_open:
        tail = "[agent-reboot-protector] agent exited."
        if context_file:
            tail += " Context snapshot: %s" % context_file
        parts.append("echo %s" % shlex.quote(tail))
        parts.append('exec "${SHELL:-/bin/sh}" -l')
    return [shell, "-lc", "; ".join(parts)]


def build_spawn_argv(entry, config):
    """The full terminal command for one manifest entry."""
    terminal = config.get("terminal") or "ghostty"
    keep_open = (config.get("restore") or {}).get("keep_shell_open", True)
    cwd = entry.get("cwd")
    kind = entry.get("kind")
    sid = entry.get("session_id")
    ctx = entry.get("context_file")

    if kind and sid:
        mod = agents.get(kind)
        resume = mod.resume_argv(sid, entry.get("agent_cmdline"), config)
        banner = "[agent-reboot-protector] resuming %s session %s" % (kind, sid)
        inner = _wrap_shell(shlex.join(resume), banner, ctx, keep_open)
    elif kind:
        mod = agents.get(kind)
        fresh = mod.fresh_argv(ctx, entry.get("agent_cmdline"), config) if ctx else None
        if fresh:
            banner = ("[agent-reboot-protector] no session id was captured for the "
                      "%s agent that ran here; starting fresh from the context "
                      "snapshot" % kind)
            inner = _wrap_shell(shlex.join(fresh), banner, ctx, keep_open)
        else:
            banner = ("[agent-reboot-protector] a %s agent ran here before the "
                      "reboot but no session could be identified" % kind)
            inner = _wrap_shell("true", banner, ctx, True)
    else:
        # Plain shell window: just reopen it in place.
        inner = None

    argv = [terminal]
    if cwd:
        argv.append("--working-directory=%s" % cwd)
    if inner:
        argv += ["-e"] + inner
    return argv


def _pending(manifest, force):
    if not manifest:
        return False, "no manifest found — run `arp catalog` first"
    if force:
        return True, None
    if manifest.get("consumed"):
        return False, "manifest already restored (use --force to redo)"
    if manifest.get("boot_id") == boot_id():
        return False, ("manifest is from the current boot — nothing to restore "
                       "(use --force to spawn anyway)")
    return True, None


def run_restore(config, force=False, dry_run=False):
    manifest = read_json(manifest_path())
    ok, reason = _pending(manifest, force)
    if not ok:
        log.info("restore skipped: %s", reason)
        return 0

    entries = manifest.get("entries") or []
    if not entries:
        log.info("manifest has no windows to restore")
        return 0

    backend = detect_backend(config)
    rcfg = config.get("restore") or {}
    if not dry_run:
        if not backend.wait_ready(rcfg.get("max_wait_wm_seconds", 180)):
            log.error("window manager did not become ready; aborting restore")
            return 1

    if manifest.get("backend") not in (backend.name, None):
        log.warning("manifest was captured with backend %r but current backend is %r; "
                    "desktop identifiers may not translate exactly",
                    manifest.get("backend"), backend.name)

    desktops = [e["desktop"] for e in entries
                if isinstance(e.get("desktop"), int)]
    if desktops and not dry_run:
        # X11 desktops are 0-based, so index N needs N+1 desktops.
        backend.ensure_desktops(max(desktops) + 1)

    delay = (rcfg.get("spawn_delay_ms", 400)) / 1000.0
    failures = 0
    for e in entries:
        argv = build_spawn_argv(e, config)
        target = e.get("desktop")
        desc = "%s%s on desktop %s" % (
            e.get("kind") or "shell",
            " (%s)" % e.get("session_id") if e.get("session_id") else "",
            target if target is not None else "?")
        if dry_run:
            print("would spawn: %-40s cwd=%s\n  %s" % (desc, e.get("cwd"), shlex.join(argv)))
            continue
        log.info("spawning %s in %s", desc, e.get("cwd"))
        try:
            wid = backend.spawn(argv, target, e.get("cwd"))
            if wid is None and backend.supports_placement:
                failures += 1
        except Exception as ex:
            log.error("spawn failed for %s: %s", desc, ex)
            failures += 1
        time.sleep(delay)

    if not dry_run:
        manifest["consumed"] = True
        manifest["restored_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        atomic_write_json(manifest_path(), manifest)
        log.info("restore complete: %d window(s), %d problem(s)", len(entries), failures)
    return 0 if failures == 0 else 1


def run_fix_desktops(config, dry_run=False):
    """Move existing terminal windows to the desktops recorded in the
    manifest, matching by title/cwd. Useful when windows came back on their
    own (session restore) but all landed on desktop 0."""
    from .catalog import _match_score  # reuse the pairing heuristic

    manifest = read_json(manifest_path())
    if not manifest:
        log.error("no manifest found")
        return 1
    backend = detect_backend(config)
    if not backend.supports_placement:
        log.error("backend %s cannot move windows", backend.name)
        return 1
    windows = backend.list_windows()
    entries = [e for e in (manifest.get("entries") or []) if e.get("desktop") is not None]
    moved = 0
    used = set()
    for e in entries:
        best, best_score = None, 0
        for w in windows:
            if w.id in used:
                continue
            score = _match_score(w.title, {"cwd": e.get("cwd"), "agent_kind": e.get("kind")})
            if score > best_score:
                best, best_score = w, score
        if best is None:
            continue
        used.add(best.id)
        if best.desktop != e["desktop"]:
            if dry_run:
                print("would move window %r (%s) -> desktop %s"
                      % (best.title[:60], best.id, e["desktop"]))
            else:
                backend.move(best.id, e["desktop"])
                moved += 1
    log.info("moved %d window(s)", moved)
    return 0
