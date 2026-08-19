"""Agent adapters: detect running agent CLIs, find their session ids,
build resume commands, and snapshot context."""

import os

from . import claude_code, copilot, cursor

MODULES = {m.KIND: m for m in (claude_code, copilot, cursor)}

# Substrings of cmdline tokens that identify each agent. Checked against every
# token so both direct binaries ("claude") and node wrappers
# ("node .../@anthropic-ai/claude-code/cli.js") are caught.
_TOKEN_MARKERS = {
    "claude-code": ("claude-code", "@anthropic-ai/claude"),
    "copilot": ("@github/copilot", "copilot-cli"),
    "cursor": ("cursor-agent",),
}
_BASENAMES = {
    "claude": "claude-code",
    "copilot": "copilot",
    "cursor-agent": "cursor",
    # Cursor CLI's binary was renamed to `agent` (cursor-agent kept as alias).
    "agent": "cursor",
}


def classify(cmdline):
    """Return the agent kind for a process cmdline, or None."""
    if not cmdline:
        return None
    for i, tok in enumerate(cmdline):
        base = os.path.basename(tok.split("=", 1)[0])
        # Only trust basenames for the executable / script tokens, not flag
        # values further down the argv.
        if i < 3 and base in _BASENAMES:
            return _BASENAMES[base]
        for kind, markers in _TOKEN_MARKERS.items():
            for m in markers:
                if m in tok:
                    return kind
    return None


def get(kind):
    return MODULES[kind]
