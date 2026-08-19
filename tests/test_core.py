"""Pure-logic tests (no window manager or agent CLIs needed).

Run with: python3 -m unittest discover -s tests
"""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arp import agents
from arp.agents import claude_code, common, cursor
from arp.catalog import _match_score, _pair_windows
from arp.restore import build_spawn_argv
from arp.util import DEFAULT_CONFIG
from arp.wm.base import Window


class TestClassify(unittest.TestCase):
    def test_claude_binary(self):
        self.assertEqual(agents.classify(["claude", "--resume", "abc"]), "claude-code")
        self.assertEqual(agents.classify(["/home/u/.local/bin/claude"]), "claude-code")

    def test_claude_node_wrapper(self):
        self.assertEqual(
            agents.classify(["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"]),
            "claude-code")

    def test_copilot(self):
        self.assertEqual(agents.classify(["copilot", "--allow-all-tools"]), "copilot")
        self.assertEqual(
            agents.classify(["node", "/x/node_modules/@github/copilot/index.js"]),
            "copilot")

    def test_cursor(self):
        self.assertEqual(agents.classify(["cursor-agent"]), "cursor")
        self.assertEqual(agents.classify(["/opt/cursor-agent/cursor-agent"]), "cursor")

    def test_non_agents(self):
        self.assertIsNone(agents.classify(["bash"]))
        self.assertIsNone(agents.classify(["vim", "claude-notes.md"]))
        self.assertIsNone(agents.classify([]))


class TestFlagPassthrough(unittest.TestCase):
    def test_bool_and_value_flags(self):
        out = common.flag_passthrough(
            ["claude", "--dangerously-skip-permissions", "--model", "opus",
             "some prompt", "--resume", "id"],
            {"--dangerously-skip-permissions"}, {"--model"})
        self.assertEqual(out, ["--dangerously-skip-permissions", "--model", "opus"])

    def test_equals_form(self):
        out = common.flag_passthrough(
            ["claude", "--model=opus"], set(), {"--model"})
        self.assertEqual(out, ["--model=opus"])

    def test_resume_strip(self):
        argv = claude_code.resume_argv(
            "sid-1", ["claude", "-c", "--verbose"], DEFAULT_CONFIG)
        self.assertEqual(argv[:3], ["claude", "--resume", "sid-1"])
        self.assertNotIn("-c", argv)
        self.assertIn("--verbose", argv)


class TestSessionMatching(unittest.TestCase):
    def test_claude_project_dir_munge(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_CONFIG_DIR"] = tmp
            try:
                proj = os.path.join(tmp, "projects", "-home-u-my-proj")
                os.makedirs(proj)
                dirs = claude_code._project_dirs("/home/u/my.proj")
                self.assertEqual(dirs, [proj])
            finally:
                del os.environ["CLAUDE_CONFIG_DIR"]

    def test_fd_beats_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_CONFIG_DIR"] = tmp
            try:
                proj = os.path.join(tmp, "projects", "-w")
                os.makedirs(proj)
                sid_a = "aaaaaaaa-1111-2222-3333-444444444444"
                sid_b = "bbbbbbbb-1111-2222-3333-444444444444"
                for sid in (sid_a, sid_b):
                    with open(os.path.join(proj, sid + ".jsonl"), "w") as f:
                        f.write(json.dumps({"timestamp": "2026-01-01T00:00:00Z"}) + "\n")
                open_paths = [os.path.join(proj, sid_a + ".jsonl")]
                cands = claude_code.session_candidates(None, open_paths, "/w", 3600)
                self.assertEqual(cands[0].session_id, sid_a)
                self.assertEqual(cands[0].confidence, "open-file")
            finally:
                del os.environ["CLAUDE_CONFIG_DIR"]

    def test_cursor_workspace_hash(self):
        # /tmp/x -> md5 hash directory lookup
        import hashlib
        h = hashlib.md5(b"/tmp/x").hexdigest()
        self.assertEqual(len(h), 32)
        sid = cursor._sid_from_path(
            os.path.expanduser("~/.cursor/chats/%s/chat-123/store.db" % h))
        self.assertEqual(sid, "chat-123")


class TestPairing(unittest.TestCase):
    def test_title_pairing(self):
        wins = [
            Window("0x1", 3, 100, "claude — ~/proj/alpha"),
            Window("0x2", 7, 100, "copilot — ~/proj/beta"),
        ]
        roots = [
            {"root": None, "agent": None, "agent_kind": "copilot",
             "cwd": os.path.expanduser("~/proj/beta")},
            {"root": None, "agent": None, "agent_kind": "claude-code",
             "cwd": os.path.expanduser("~/proj/alpha")},
        ]
        pairs = _pair_windows(wins, roots)
        by_kind = {r["agent_kind"]: w for w, r in pairs if w}
        self.assertEqual(by_kind["claude-code"].desktop, 3)
        self.assertEqual(by_kind["copilot"].desktop, 7)

    def test_match_score(self):
        self.assertGreater(
            _match_score("claude — ~/work/api", {"cwd": os.path.expanduser("~/work/api"),
                                                 "agent_kind": "claude-code"}),
            _match_score("claude — ~/work/api", {"cwd": "/somewhere/else",
                                                 "agent_kind": "copilot"}))


class TestSpawnArgv(unittest.TestCase):
    def test_resume_spawn(self):
        entry = {"cwd": "/home/u/proj", "kind": "claude-code",
                 "session_id": "sid-1", "agent_cmdline": ["claude"],
                 "context_file": "/tmp/ctx.md"}
        argv = build_spawn_argv(entry, DEFAULT_CONFIG)
        self.assertEqual(argv[0], "ghostty")
        self.assertIn("--working-directory=/home/u/proj", argv)
        joined = " ".join(argv)
        self.assertIn("claude --resume sid-1", joined)
        self.assertIn("-e", argv)

    def test_plain_shell_spawn(self):
        entry = {"cwd": "/home/u/notes", "kind": None, "session_id": None}
        argv = build_spawn_argv(entry, DEFAULT_CONFIG)
        self.assertEqual(argv, ["ghostty", "--working-directory=/home/u/notes"])


class TestContextExtraction(unittest.TestCase):
    def test_claude_transcript_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CLAUDE_CONFIG_DIR"] = tmp
            try:
                path = os.path.join(tmp, "s.jsonl")
                lines = [
                    {"type": "user", "cwd": "/w", "gitBranch": "main",
                     "message": {"content": "please fix the login bug"}},
                    {"type": "assistant",
                     "message": {"content": [{"type": "text",
                                              "text": "Working on it; next I'll add tests."}]}},
                ]
                with open(path, "w") as f:
                    for l in lines:
                        f.write(json.dumps(l) + "\n")
                ctx = claude_code.extract_context(path, "sid")
                self.assertEqual(ctx["last_user"], "please fix the login bug")
                self.assertIn("add tests", ctx["last_assistant"])
                self.assertEqual(ctx["git_branch"], "main")
            finally:
                del os.environ["CLAUDE_CONFIG_DIR"]


if __name__ == "__main__":
    unittest.main()
