#!/usr/bin/env bash
# Install agent-reboot-protector for the current user.
#  - symlinks `arp` into ~/.local/bin
#  - registers `arp login` (restore-then-watch) as a login item:
#      Linux: XDG autostart entry (~/.config/autostart)
#      macOS: LaunchAgent (~/Library/LaunchAgents)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
UNINSTALL=0
[[ "${1:-}" == "--uninstall" ]] && UNINSTALL=1

os="$(uname -s)"

if [[ $UNINSTALL -eq 1 ]]; then
  rm -f "${BIN_DIR}/arp"
  rm -f "${HOME}/.config/autostart/agent-reboot-protector.desktop"
  if [[ "$os" == "Darwin" ]]; then
    launchctl unload "${HOME}/Library/LaunchAgents/com.agent-reboot-protector.login.plist" 2>/dev/null || true
    rm -f "${HOME}/Library/LaunchAgents/com.agent-reboot-protector.login.plist"
  fi
  echo "uninstalled (state in ~/.local/state/agent-reboot-protector was kept)"
  exit 0
fi

command -v python3 >/dev/null || { echo "error: python3 is required" >&2; exit 1; }

mkdir -p "${BIN_DIR}"
ln -sf "${REPO}/bin/arp" "${BIN_DIR}/arp"
chmod +x "${REPO}/bin/arp"
echo "installed: ${BIN_DIR}/arp -> ${REPO}/bin/arp"
case ":$PATH:" in
  *":${BIN_DIR}:"*) ;;
  *) echo "note: add ${BIN_DIR} to your PATH" ;;
esac

if [[ "$os" == "Darwin" ]]; then
  PLIST="${HOME}/Library/LaunchAgents/com.agent-reboot-protector.login.plist"
  mkdir -p "$(dirname "$PLIST")"
  sed "s|@ARP@|${BIN_DIR}/arp|g" "${REPO}/launchd/com.agent-reboot-protector.login.plist" > "$PLIST"
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "installed LaunchAgent: $PLIST"
  if ! command -v yabai >/dev/null; then
    echo "note: install yabai for Space (desktop) placement on macOS — without it"
    echo "      sessions are restored but windows cannot be moved to Spaces."
  fi
else
  AUTOSTART_DIR="${HOME}/.config/autostart"
  mkdir -p "$AUTOSTART_DIR"
  sed "s|@ARP@|${BIN_DIR}/arp|g" "${REPO}/autostart/agent-reboot-protector.desktop" \
    > "${AUTOSTART_DIR}/agent-reboot-protector.desktop"
  echo "installed autostart entry: ${AUTOSTART_DIR}/agent-reboot-protector.desktop"
  if [[ -n "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]] && ! command -v wmctrl >/dev/null; then
    echo "note: install wmctrl for desktop placement on X11 (e.g. apt install wmctrl)"
  fi
fi

echo
echo "done. next steps:"
echo "  arp catalog     # take a snapshot right now (manual trigger)"
echo "  arp status      # see what was captured"
echo "  arp watch       # or just log out/in — the login item runs restore+watch"
