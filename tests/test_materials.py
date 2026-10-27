"""時間のかかる材料（Issue・最後の返事）を、プラグインが頼んだときだけ取ることのテスト。"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktree_board import agents, cards, collect, config, sources
from worktree_board.judge import issue_of
from worktree_board.model import Column, Turn
from worktree_board.server import Board, Watch
from worktree_board.sources import FetchError, Source

ME = "me"


def pr(**kw) -> dict:
    return {"number": 1, "title": "t", "url": "u", "headRefName": "feature/12-x", "headRefOid": "h1", "isDraft": False,
            "mergeable": "MERGEABLE", "reviews": [], "reviewRequests": [], "statusCheckRollup": [], **kw}


def issue_node(n, typename="Issue"):
    return {"__typename": typename, "number": n, "title": f"issue {n}", "state": "OPEN", "stateReason": None, "url": f"u{n}",
            "updatedAt": "2026-01-01T00:00:00Z", "labels": {"nodes": [{"name": "bug"}]}, "assignees": {"nodes": [{"login": "me"}]}}


class IssueNumbers(unittest.TestCase):
    def test_number_between_separators(self):
        for branch, want in [("feature/5323-smartchat", 5323), ("fix/12_y", 12), ("5-fix", 5), ("feat/ABC-123-x", 123), ("#42", 42),
                             ("main", None), ("release/2.1", None), ("deps/lodash-4.17.21", None), ("v2-foo", None), (None, None)]:
            self.assertEqual(issue_of(branch), want, branch)


class Graphql(unittest.TestCase):
    def answer(self, data, errors=(), code=0):
        out = json.dumps({"data": data, **({"errors": list(errors)} if errors else {})})
        return subprocess.CompletedProcess([], code, out, "gh: error" if code else "")

    def test_missing_numbers_and_prs_are_left_out(self):
        data = {"repository": {"i5": issue_node(5), "i6": {"__typename": "PullRequest"}, "i7": None}}
        with mock.patch.object(sources.subprocess, "run", return_value=self.answer(data, [{"type": "NOT_FOUND"}], 1)) as run:
            got = Source(Path("."), "o/r").issues({5, 6, 7, None, True})
        self.assertEqual(sorted(got), [5])
        self.assertEqual((got[5]["labels"], got[5]["assignees"]), (["bug"], ["me"]))
        self.assertIn('repository(owner: "o", name: "r")', run.call_args.args[0][-1])

    def test_other_errors_fail_and_big_sets_are_split(self):
        with mock.patch.object(sources.subprocess, "run", return_value=self.answer(None, [{"type": "FORBIDDEN"}], 1)):
            with self.assertRaises(FetchError):
                Source(Path("."), "o/r").issues({5})
        with mock.patch.object(sources, "ISSUES_PER_QUERY", 2), \
             mock.patch.object(sources.subprocess, "run", return_value=self.answer({"repository": {}})) as run:
            Source(Path("."), "o/r").issues({1, 2, 3})
        self.assertEqual(run.call_count, 2)
        with mock.patch.object(sources.subprocess, "run") as run:
            self.assertEqual(Source(Path("."), "o/r").issues(set()), {})  # 番号が無ければ問い合わせない
        run.assert_not_called()


class FakeSource:
    label = "o/r"

    def __init__(self, plugin):
        self.plugin, self.asked = plugin, []

    def fetch(self):
        return {"worktrees": {"feature/12-x": "/w/x", "wip": "/w/wip"}, "primary": {}, "prs": [pr()], "closed": {}, "me": ME}

    def issues(self, numbers):
        self.asked.append(set(numbers))
        return {12: {"number": 12, "title": "issue 12", "labels": ["bug"]}}


def plugin(state=lambda wt: Turn("mine", wt.issue["title"] if wt.issue else "-"), needs=(), issue_of=issue_of, fetch=None):
    return cards.Plugin(state, (Column("mine"),), None, frozenset(needs), issue_of, fetch)


class Fetching(unittest.TestCase):
    def test_issues_are_fetched_only_when_asked(self):
        quiet = FakeSource(plugin())
        got, error = Watch(quiet).fetch()
        self.assertEqual((error, quiet.asked, got["issue_numbers"]), (None, [], {"feature/12-x": 12, "wip": None}))
        asking = FakeSource(plugin(needs=["issue"]))
        board = Board([asking], sessions=lambda: [], host="h")
        board.tick()
        self.assertEqual(asking.asked, [{12, None}])
        by = {i["label"]: i for i in board.view()["worktrees"]}
        self.assertEqual((by["x"]["issue"], by["x"]["reason"]), (12, {"text": "issue 12"}))
        self.assertEqual((by["wip"]["issue"], by["wip"]["reason"]), (None, {"text": "-"}))

    def test_broken_issue_of_is_a_repo_error(self):
        def bad(branch):
            raise ValueError("no")
        _, error = Watch(FakeSource(plugin(issue_of=bad))).fetch()
        self.assertEqual(error["key"], "error.plugin_issue_of")
        self.assertIn("ValueError: no", error["args"]["detail"])
        _, error = Watch(FakeSource(plugin(issue_of=lambda b: "12"))).fetch()
        self.assertIn("returned str", error["args"]["detail"])

    def test_plugin_fetch_runs_each_refresh_and_reaches_every_worktree(self):
        calls = []

        def fetch(repo, prs):
            calls.append((repo, [p["number"] for p in prs]))
            prs[0]["number"] = 0  # 書き換えても、テンプレートの材料は変わらない
            return {"label": "bug"}
        board = Board([FakeSource(plugin(state=lambda wt: Turn("mine", wt.fetched["label"]), fetch=fetch))],
                      sessions=lambda: [], host="h")
        board.tick()
        board.tick()
        self.assertEqual(calls, [("o/r", [1]), ("o/r", [1])])
        self.assertEqual({i["label"]: i["reason"]["text"] for i in board.view()["worktrees"]}, {"x": "bug", "wip": "bug"})
        self.assertIsNone(Watch(FakeSource(plugin())).fetch()[0]["fetched"])

    def test_broken_fetch_is_a_repo_error_and_keeps_the_last_cards(self):
        broken = []

        def fetch(repo, prs):
            if broken:
                raise RuntimeError("gh が止まった")
            return "前の材料"
        board = Board([FakeSource(plugin(state=lambda wt: Turn("mine", wt.fetched), fetch=fetch))], sessions=lambda: [], host="h")
        board.tick()
        broken.append(True)
        board.tick()
        error = board.errors()[0]["error"]
        self.assertEqual((error["key"], error["args"]["detail"]), ("error.plugin_fetch", "RuntimeError: gh が止まった"))
        self.assertEqual({i["reason"]["text"] for i in board.view()["worktrees"]}, {"前の材料"})


def transcript(lines) -> str:
    return "".join(json.dumps(x) + "\n" for x in lines)


def said(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


class LastReply(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.file = Path(tmp.name) / "s.jsonl"

    def test_last_text_is_found_past_large_tool_output(self):
        tool = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}
        result = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x" * 5000}]}}
        self.file.write_text(transcript([said("古い返事"), said("❓ 確認: どちらにしますか"), tool] + [result] * 20))
        with mock.patch.object(agents, "TAIL_FIRST", 1024):  # 最初の読みでは見つからず、読む量を増やして見つける
            self.assertEqual(agents.last_reply({"transcript": str(self.file)}), "❓ 確認: どちらにしますか")
        with mock.patch.object(agents, "open", side_effect=AssertionError("読み直した"), create=True):
            self.assertEqual(agents.last_reply({"transcript": str(self.file)}), "❓ 確認: どちらにしますか")  # 変わっていなければ読み直さない
        self.assertIsNone(agents.last_reply({"transcript": str(self.file) + ".none"}))
        self.assertIsNone(agents.last_reply({"transcript": None}))

    def test_reply_is_read_only_when_the_plugin_asks(self):
        self.file.write_text(transcript([said("✅ 完了: 終わった")]))
        got = {"worktrees": {"wip": "/w/wip"}, "prs": [], "closed": {}, "me": ME}
        by_path = {"/w/wip": [{"agent": "Claude Code", "status": "idle", "transcript": str(self.file)}]}
        quiet = collect.local("o/r", got, by_path, "h")
        asking = collect.local("o/r", got, by_path, "h", frozenset({"last_reply"}))
        self.assertEqual((quiet[0].sessions[0].last_reply, asking[0].sessions[0].last_reply), (None, "✅ 完了: 終わった"))

    def test_transcript_path_follows_claude_code(self):
        self.assertEqual(agents.transcript_path(Path("/c"), "/Users/a/my_repo.x", "id"), Path("/c/projects/-Users-a-my-repo-x/id.jsonl"))


class Needs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def load(self, body):
        (self.root / "p.py").write_text(body)
        (self.root / "config.toml").write_text('[[repos]]\npath = "a"\nplugin = "p.py"\n')
        return config.load(self.root / "config.toml").repos[0].plugin

    def test_needs_and_issue_of_are_read(self):
        p = self.load("NEEDS = ['issue', 'last_reply']\ndef issue_of(b):\n    return 7\ndef state(wt):\n    return None\n")
        self.assertEqual((p.needs, p.issue_of("x")), (frozenset({"issue", "last_reply"}), 7))
        self.assertEqual(self.load("def state(wt):\n    return None\n").needs, frozenset())
        for bad in ("'issue'", "['issues']", "3"):
            with self.assertRaises(config.ConfigError) as e:
                self.load(f"NEEDS = {bad}\ndef state(wt):\n    return None\n")
            self.assertEqual(e.exception.key, "error.plugin_needs", bad)

    def test_fetch_is_read(self):
        self.assertEqual(self.load("def fetch(repo, prs):\n    return repo\ndef state(wt):\n    return None\n").fetch("o/r", []), "o/r")
        self.assertIsNone(self.load("def state(wt):\n    return None\n").fetch)
        with self.assertRaises(config.ConfigError) as e:
            self.load("fetch = 3\ndef state(wt):\n    return None\n")
        self.assertEqual((e.exception.key, e.exception.params["detail"]), ("error.plugin_load", "fetch is not a function"))


if __name__ == "__main__":
    unittest.main()
