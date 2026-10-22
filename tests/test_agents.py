import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from worktree_board import agents, cards, collect
from worktree_board.judge import judge
from worktree_board.model import Session, Text
from worktree_board.server import Board

ME = "me"


def session(status, cwd="/w/a", waiting_for=None):
    """agents.sessions() が返す 1 件の形。"""
    return {"agent": "Claude Code", "status": status, "waiting_for": waiting_for, "since": None, "cwd": cwd}


def talk(status, waiting_for=None):
    """判定が受け取る会話。"""
    return Session("Claude Code", status, waiting_for)


def pr(**kw) -> dict:
    return {"number": 1, "title": "t", "url": "u", "headRefName": "feat", "headRefOid": "h1", "isDraft": False,
            "mergeable": "MERGEABLE", "reviews": [], "reviewRequests": [], "statusCheckRollup": [], **kw}


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", ""])
    p.wait()
    return p.pid


class ClaudeSessions(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        (self.base / "sessions").mkdir()

    def write(self, name, body):
        (self.base / "sessions" / name).write_text(body if isinstance(body, str) else json.dumps(body))

    def test_reads_live_sessions_and_skips_the_rest(self):
        me = os.getpid()
        self.write("1.json", {"pid": me, "cwd": "/w/a", "status": "busy", "statusUpdatedAt": 1792651700989})
        self.write("2.json", {"pid": me, "cwd": "/w/b", "status": "waiting", "waitingFor": "input needed"})
        self.write("3.json", {"pid": me, "cwd": "/w/c", "status": "idle"})
        self.write("4.json", {"pid": dead_pid(), "cwd": "/w/d", "status": "busy"})  # 落ちた会話のファイル
        self.write("5.json", {"pid": me, "cwd": "/w/e", "status": "compacting"})  # 知らない状態
        self.write("6.json", {"pid": me, "status": "busy"})  # 作業フォルダが無い
        self.write("7.json", "{broken")  # 書き換えの途中
        self.write("8.json", ["not", "a", "dict"])
        self.write("9.key", "secret")  # 会話の鍵など、json 以外は読まない
        self.write("10.json", {"pid": me, "cwd": "/w/f", "status": "shell"})  # シェルのコマンドを走らせている間
        got = {s["cwd"]: s for s in agents.claude_sessions(self.base)}
        self.assertEqual({c: s["status"] for c, s in got.items()},
                         {"/w/a": "working", "/w/b": "waiting", "/w/c": "idle", "/w/f": "working"})
        self.assertEqual(got["/w/b"]["waiting_for"], "input needed")
        self.assertEqual(got["/w/a"]["since"], "2026-10-22T06:48:20+00:00")
        self.assertEqual(got["/w/a"]["agent"], "Claude Code")

    def test_missing_folder_means_no_sessions(self):
        self.assertEqual(agents.claude_sessions(self.base / "nope"), [])

    def test_config_dir_can_be_moved(self):
        self.assertEqual(agents.claude_dir({"CLAUDE_CONFIG_DIR": "/x/claude"}), Path("/x/claude"))
        self.assertEqual(agents.claude_dir({}), Path.home() / ".claude")

    def test_alive_rejects_odd_pids(self):
        for pid in (None, "1", 0, -1, True):
            self.assertFalse(agents.alive(pid), pid)
        self.assertTrue(agents.alive(os.getpid()))


class Assign(unittest.TestCase):
    def test_deepest_path_wins_and_outsiders_are_dropped(self):
        found = [session("working", "/w/repo/src"), session("idle", "/w/repo/.worktrees/x/lib"),
                 session("working", "/w/repo-other"), session("working", "/elsewhere")]
        by = agents.assign(found, ["/w/repo", "/w/repo/.worktrees/x"])
        self.assertEqual({p: [s["cwd"] for s in ss] for p, ss in by.items()},
                         {"/w/repo": ["/w/repo/src"], "/w/repo/.worktrees/x": ["/w/repo/.worktrees/x/lib"]})

    def test_symlinked_paths_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            (real / "sub").mkdir(parents=True)
            link = Path(tmp) / "link"
            link.symlink_to(real)
            by = agents.assign([session("working", str(real / "sub"))], [str(link)])
            self.assertEqual(list(by), [str(link)])


class JudgeWithAgents(unittest.TestCase):
    def test_waiting_comes_first_even_after_merge(self):
        r = judge(None, {"number": 3, "state": "MERGED"}, ME, (talk("working"), talk("waiting", "input needed")))
        self.assertEqual((r.column, r.reason), ("mine", Text("reason.agent_waiting_for", {"agent": "Claude Code", "what": "input needed"})))
        self.assertEqual(r.next, Text("next.answer_agent"))
        self.assertEqual(judge(pr(), None, ME, (talk("waiting"),)).reason.key, "reason.agent_waiting")

    def test_working_agent_beats_pr_state(self):
        failed = pr(statusCheckRollup=[{"__typename": "StatusContext", "context": "x", "state": "FAILURE"}])
        self.assertEqual(judge(failed, None, ME, (talk("working"),)).column, "agent")
        self.assertEqual(judge(None, None, ME, (talk("working"),)).column, "agent")

    def test_idle_agent_leaves_the_pr_state(self):
        self.assertEqual(judge(pr(), None, ME, (talk("idle"),)).column, "review")
        self.assertEqual(judge(None, None, ME, (talk("idle"),)).reason.key, "reason.no_pr")


class PrimaryCheckout(unittest.TestCase):
    def items(self, found, prs=()):
        got = {"worktrees": {"feat": "/w/feat"}, "primary": {"path": "/w/repo", "branch": "main"},
               "prs": list(prs), "closed": {}, "me": ME}
        return cards.build(cards.DEFAULT, collect.local("o/r", got, agents.assign(found, ["/w/repo", "/w/feat"]), "h"))

    def test_primary_shows_only_while_an_agent_works_or_waits(self):
        self.assertEqual([i["label"] for i in self.items([])], ["feat"])
        self.assertEqual([i["label"] for i in self.items([session("idle", "/w/repo")])], ["feat"])
        items = {i["label"]: i for i in self.items([session("working", "/w/repo/src")])}
        self.assertEqual(items["repo"]["state"], "agent")
        self.assertEqual((items["repo"]["badges"], items["repo"]["primary"], items["repo"]["title"]), ([], True, "main"))

    def test_pr_from_the_primary_branch_is_one_card_on_the_primary(self):
        items = self.items([session("waiting", "/w/repo")], [pr(number=7, headRefName="main")])
        self.assertEqual(sorted(i["label"] for i in items), ["feat", "repo"])
        # 会話が無くても、primary checkout の branch に開いた PR があれば、primary checkout のカードとして出す
        items = {i["label"]: i for i in self.items([], [pr(number=7, headRefName="main")])}
        self.assertEqual((sorted(items), items["repo"]["pr"], items["repo"]["badges"]), (["feat", "repo"], 7, []))

    def test_idle_session_is_listed_on_the_card(self):
        items = {i["label"]: i for i in self.items([session("idle", "/w/feat")])}
        self.assertEqual(items["feat"]["agents"], [{"agent": "Claude Code", "status": "idle", "waiting_for": None, "since": None}])
        self.assertEqual(items["feat"]["state"], "mine")


class FakeSource:
    label = "o/r"
    plugin = None

    def fetch(self):
        return {"worktrees": {"feat": "/w/feat"}, "primary": {"path": "/w/repo", "branch": "main"},
                "prs": [pr()], "closed": {}, "me": ME}


class BoardReadsSessionsOnEveryView(unittest.TestCase):
    def test_agent_state_changes_without_a_new_fetch(self):
        found = []
        board = Board([FakeSource()], sessions=lambda: list(found), host="h")
        board.tick()
        self.assertEqual([i["state"] for i in board.view()["worktrees"]], ["review"])
        found.append(session("working", "/w/feat"))
        self.assertEqual([i["state"] for i in board.view()["worktrees"]], ["agent"])
        found[0] = session("waiting", "/w/feat")
        found.append(session("working", "/w/repo"))
        self.assertEqual(sorted((i["label"], i["state"]) for i in board.view()["worktrees"]),
                         [("feat", "mine"), ("repo", "agent")])


if __name__ == "__main__":
    unittest.main()
