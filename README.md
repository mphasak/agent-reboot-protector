# agent-reboot-protector

Forced OS updates keep rebooting your machine and nuking a dozen-plus terminal
agent sessions spread across virtual desktops? This tool catalogs every
running agent session **before** the reboot and rebuilds the whole layout
**after** it:

- every Ghostty window goes back to the **virtual desktop it was on** (instead
  of all piling up on desktop 0),
- each window reopens in the **folder it was in**,
- and the terminal **resumes the exact agent session** that was running there —
  `claude --resume <id>`, `copilot --resume=<id>`, or `cursor-agent --resume=<id>` —
  even when several sessions live in the same folder.

It also writes a per-session **context snapshot** (last messages, task list /
next steps, git branch) so that even if a session can't be resumed by id, the
replacement agent can be pointed at the snapshot and pick the work back up.

Supported agents: **Claude Code** (`claude`), **GitHub Copilot CLI**
(`copilot`), **Cursor CLI** (`agent` / `cursor-agent`). Plain shell windows
(no agent) are restored too — right desktop, right folder.

No dependencies beyond Python 3.8+ and your window manager's CLI tooling (see
the support matrix below).

## Quick start

```sh
git clone <this repo> ~/tools/agent-reboot-protector
cd ~/tools/agent-reboot-protector
./install.sh
arp catalog     # take a snapshot right now
arp status      # see what it captured
```

`install.sh` symlinks `arp` into `~/.local/bin` and registers `arp login` as a
login item (XDG autostart on Linux, LaunchAgent on macOS). From then on:

1. **While you work**, `arp watch` (started by the login item) re-snapshots
   every 5 minutes, and immediately when it detects a scheduled
   shutdown/reboot (systemd-logind's scheduled shutdown on Linux, `pmset`
   sched on macOS). You can always force a snapshot with `arp catalog`.
2. **After the reboot**, the login item runs `arp restore`: it waits for the
   window manager, then spawns one Ghostty window per manifest entry on the
   correct desktop, in the correct folder, running the correct resume command.
   The manifest is marked consumed so logging out/in again doesn't
   double-spawn.

## Commands

| command | what it does |
|---|---|
| `arp catalog` | snapshot all windows/agents/sessions now (manual trigger) |
| `arp watch` | daemon: periodic snapshots + scheduled-shutdown detection |
| `arp restore` | rebuild everything from the last pre-reboot manifest |
| `arp restore --dry-run` | print exactly what would be spawned |
| `arp login` | what the login item runs: restore (if pending), then watch |
| `arp fix-desktops` | don't respawn anything; just move existing Ghostty windows back to their manifest desktops (for when your DE restored the windows itself but dumped them on desktop 0) |
| `arp status` | show the current manifest |
| `arp paths` | print state/config locations |

## How a session is identified

For each Ghostty window the catalog walks the process tree (window → shell →
agent process) and then works out *which* session that specific process is on,
most-confident strategy first:

1. **Open file handles** — the session transcript/state file the agent
   process actually holds open (`/proc/<pid>/fd`, `lsof` on macOS). This is
   exact, and is what disambiguates two sessions running in the same folder.
2. **Workspace-scoped stores** — Claude Code's
   `~/.claude/projects/<munged-cwd>/*.jsonl`, Copilot's
   `~/.copilot/session-state/<id>/workspace.yaml` (records the cwd), Cursor's
   `~/.cursor/chats/<md5-of-cwd>/<chat-id>/`.
3. **Recency + process start time** — a session file actively being written,
   or whose first entry lines up with when the agent process started.

Each entry records its confidence (`open-file`, `cwd-match`,
`workspace-match`, `start-time-match`, `active-mtime`, …) — visible in
`arp status`. Session ids are claimed uniquely per snapshot, so two windows
never resume the same session.

If no id can be determined, the window is restored with a **fallback**: Claude
Code and Cursor are started fresh with a prompt pointing at the context
snapshot ("read this file and continue"); Copilot falls back to
`copilot --continue` (most recent session for that directory).

## What gets captured per window

Stored under `~/.local/state/agent-reboot-protector/` (Linux) or
`~/Library/Application Support/agent-reboot-protector/` (macOS):

- `manifest.json` — desktop, window title, folder, agent kind, pid, original
  command line, session id + confidence, context file path;
- `contexts/<timestamp>/desktop-N-<agent>-<id>.md` — session summary, task
  list / next steps, last user and assistant messages, git branch, raw state
  paths;
- `history/` — the last 30 manifests, in case one gets clobbered.

## Desktop placement support matrix

| environment | catalog desktops | move/place windows | notes |
|---|---|---|---|
| X11 (any EWMH WM) | ✅ | ✅ | needs `wmctrl`; grows the desktop count if needed |
| Hyprland | ✅ | ✅ | via `hyprctl`; spawns directly onto the target workspace |
| Sway | ✅ | ✅ | via `swaymsg` |
| macOS + [yabai](https://github.com/koekeishiya/yabai) | ✅ | ✅ | creates missing Spaces |
| macOS without yabai | ⚠️ titles only | ❌ | macOS has no public Spaces API; sessions still resume, placement is manual |
| GNOME/KDE on Wayland | ❌ | ❌ | no window enumeration API; sessions are still cataloged from the process tree and resumed, without placement |

On X11 desktops are the 0-based indexes you know from `wmctrl -d`; on
Hyprland/Sway the native workspace ids/names are used.

Ghostty note: with `gtk-single-instance` every window shares one process, so
window↔shell matching falls back to title heuristics. It works (Ghostty titles
show the running program / directory), but if you want exact matching, set
`gtk-single-instance = false` in your Ghostty config.

## Scheduled-reboot detection

- **Linux**: reads `/run/systemd/shutdown/scheduled` (written by
  `shutdown -r`, and by update managers that schedule restarts through
  logind), falling back to the logind D-Bus property. When a shutdown is
  inside the lead window (default 20 min), snapshots refresh every minute
  until the reboot.
- **macOS**: checks `pmset -g sched` for scheduled restarts.
- **Either way**, the periodic snapshot (default every 5 min) is the real
  safety net — even a surprise MDM reboot loses at most a few minutes of
  layout drift. A snapshot that suddenly finds *zero* windows never
  overwrites a non-empty manifest from the same boot (protects against the
  WM tearing windows down mid-shutdown).

## Configuration

`~/.config/agent-reboot-protector/config.json` (all optional):

```json
{
  "terminal": "ghostty",
  "periodic_minutes": 5,
  "shutdown_lead_minutes": 20,
  "active_session_window_minutes": 360,
  "backend": null,
  "restore": {
    "keep_shell_open": true,
    "max_wait_wm_seconds": 180,
    "spawn_delay_ms": 400
  },
  "agents": {
    "claude-code": {"extra_args": []},
    "copilot": {"extra_args": []},
    "cursor": {"extra_args": []}
  }
}
```

- `backend` forces `x11` / `hyprland` / `sway` / `macos` / `generic` instead
  of autodetection.
- `agents.<kind>.extra_args` is appended to every generated resume command
  (e.g. `["--dangerously-skip-permissions"]`). Recognized safe flags from the
  original command line (model, permission flags, …) are carried over
  automatically.
- `keep_shell_open` wraps the resume command so the window drops to your
  shell when the agent exits instead of closing.

## Testing

```sh
python3 -m unittest discover -s tests
arp catalog && arp status          # live snapshot of your real desktop
arp restore --dry-run --force      # print the exact spawn commands
```

## Limitations / honesty section

- Session **ids** survive reboots (Claude Code resumes append to the same
  session; Copilot and Cursor resume by stored id), but the agent's
  **in-memory state** obviously doesn't — anything not yet persisted by the
  agent itself is gone. The context snapshots exist to bridge exactly that
  gap.
- The on-disk session layouts of all three CLIs are undocumented and can
  change between releases; every adapter is multi-strategy and fails soft
  (worst case: a window is restored in the right place with a context-file
  prompt instead of a hard resume).
- Windows/WSL isn't supported (Ghostty doesn't run there anyway).
- `arp restore` spawns fresh windows; if your desktop environment *also*
  restored old Ghostty windows on its own, those strays are left alone —
  close them, or skip respawning entirely and use `arp fix-desktops`.
