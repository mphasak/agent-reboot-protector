"""Command-line interface."""

import argparse
import json
import sys

from . import __version__
from .util import (boot_id, load_config, log, manifest_path, read_json,
                   setup_logging, state_dir)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="arp",
        description="agent-reboot-protector: catalog terminal agent sessions "
                    "before a reboot and rebuild them (right desktop, right "
                    "folder, right session) after it.")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("catalog", help="snapshot all agent sessions now (manual trigger)")

    p = sub.add_parser("restore", help="rebuild windows/sessions from the last manifest")
    p.add_argument("--force", action="store_true",
                   help="restore even if the manifest is from this boot or already used")
    p.add_argument("--dry-run", action="store_true",
                   help="print what would be spawned without doing it")

    p = sub.add_parser("watch", help="run the snapshot daemon (periodic + "
                                     "scheduled-shutdown detection)")
    p.add_argument("--once", action="store_true", help="take one snapshot pass and exit")

    sub.add_parser("login", help="run at login: restore if a reboot happened, "
                                 "then keep watching")

    p = sub.add_parser("fix-desktops", help="move existing terminal windows back to "
                                            "their manifest desktops (no respawn)")
    p.add_argument("--dry-run", action="store_true")

    sub.add_parser("status", help="show the current manifest summary")
    sub.add_parser("paths", help="print state/config file locations")

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    config = load_config()

    if args.cmd == "catalog":
        from .catalog import run_catalog
        run_catalog(config)
        return 0
    if args.cmd == "restore":
        from .restore import run_restore
        return run_restore(config, force=args.force, dry_run=args.dry_run)
    if args.cmd == "watch":
        from .watch import run_watch
        return run_watch(config, once=args.once)
    if args.cmd == "login":
        from .restore import run_restore
        from .watch import run_watch
        try:
            run_restore(config)
        except Exception as e:
            log.error("restore at login failed: %s", e)
        return run_watch(config)
    if args.cmd == "fix-desktops":
        from .restore import run_fix_desktops
        return run_fix_desktops(config, dry_run=args.dry_run)
    if args.cmd == "status":
        return _status()
    if args.cmd == "paths":
        from .util import config_path
        print("state:    %s" % state_dir())
        print("manifest: %s" % manifest_path())
        print("config:   %s" % config_path())
        return 0
    return 2


def _status():
    manifest = read_json(manifest_path())
    if not manifest:
        print("no manifest yet — run `arp catalog` or start `arp watch`")
        return 1
    same_boot = manifest.get("boot_id") == boot_id()
    print("captured:  %s (backend %s)" % (manifest.get("created_at"),
                                          manifest.get("backend")))
    print("boot:      %s" % ("this boot" if same_boot else "BEFORE last reboot"))
    print("consumed:  %s" % manifest.get("consumed"))
    entries = manifest.get("entries") or []
    print("windows:   %d" % len(entries))
    for e in entries:
        sid = e.get("session_id")
        print("  desktop %-4s %-12s %-38s %s" % (
            e.get("desktop") if e.get("desktop") is not None else "?",
            e.get("kind") or "shell",
            (e.get("cwd") or "")[-38:],
            "%s (%s)" % (sid[:8], e.get("session_confidence")) if sid else "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
