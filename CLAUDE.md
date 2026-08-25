# agent-reboot-protector

`arp` catalogs terminal agent sessions (Claude Code / Copilot CLI / Cursor CLI)
running in Ghostty windows before a reboot, and rebuilds them afterwards on the
right virtual desktop, in the right folder, resuming the right session id.

## Layout

- `arp/catalog.py` — snapshot: windows -> shells -> agent processes -> session ids
- `arp/restore.py` — rebuild windows and resume commands after a reboot
- `arp/watch.py` — periodic + scheduled-shutdown snapshot daemon
- `arp/agents/` — per-agent adapters (session discovery, resume argv, context)
- `arp/wm/` — window-manager backends (x11, hyprland, sway, macos, generic)
- `tests/test_core.py` — pure-logic tests, no window manager needed

## Working on this repo

Run the tests with `python3 -m unittest discover -s tests`. They are
dependency-free and must stay that way — no third-party imports in `arp/`.

The on-disk session layouts of all three agent CLIs are undocumented and change
between releases. Every adapter is multi-strategy and must fail soft: a missing
or reshaped store degrades to a lower-confidence match or a context-file
fallback, never an exception that aborts the snapshot.

## Scheduled work and check-ins

Do not create scheduled tasks, cron jobs, recurring routines, or self check-ins
that run on the Fable model — `send_later` check-ins, `CronCreate` jobs,
`/loop`, or timer-based PR-watch polling. Unattended recurring turns burn quota
fast, because each firing re-reads the whole conversation.

After opening or subscribing to a PR, report status and stop; let webhook events
wake the session instead of polling on a timer. Schedule recurring work only
when the user asks for it in that turn, and say which model it will run on
first. A standing PR-watch subscription is fine — it costs nothing while idle.
