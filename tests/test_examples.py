"""examples/ のプラグインの見本が、読み込めて書いてあるとおりに動くこと（AGENTS.md が指すので、壊れたままにしない）。"""
import unittest
from pathlib import Path

from worktree_board import cards, config
from worktree_board.model import Session, Worktree

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def pr(**kw) -> dict:
    return {"number": 1, "title": "t", "url": "u", "headRefName": "feature/12-x", "headRefOid": "h1", "isDraft": False,
            "mergeable": "MERGEABLE", "reviews": [], "reviewRequests": [], "statusCheckRollup": [], **kw}


def load(name) -> cards.Plugin:
    return config.load_plugin(EXAMPLES / name, 0)


def worktree(**kw) -> Worktree:
    return Worktree(**{"host": "h", "repo": "o/r", "name": "x", "path": "/w/x", "branch": "feature/12-x", "pr": pr(), **kw})


class Examples(unittest.TestCase):
    def test_every_example_loads(self):
        names = sorted(p.name for p in EXAMPLES.glob("*.py"))
        self.assertGreaterEqual(len(names), 2)
        for name in names:
            load(name)

    def test_reply_mark(self):
        p = load("reply_mark.py")
        self.assertEqual(p.needs, frozenset({"last_reply"}))
        asked = worktree(sessions=(Session("Claude Code", "idle", last_reply="❓ 確認: A と B のどちらにしますか\n本文"),))
        self.assertEqual((p.state(asked).column, p.state(asked).reason), ("mine", "Claude Code が質問している: 確認: A と B のどちらにしますか"))
        later = worktree(sessions=(Session("Claude Code", "idle", last_reply="⏳ 途中: CI を待っている"),))
        self.assertEqual(p.state(later).column, "agent")
        done = worktree(sessions=(Session("Claude Code", "idle", last_reply="✅ 完了: 直した"),))
        self.assertEqual(p.state(done).column, "review")  # 印が無ければ既定の判定のまま
        # primary checkout でも、質問して止まっていれば出す（既定の判定は出さない）
        main = worktree(primary=True, pr=None, branch="main", sessions=asked.sessions)
        self.assertEqual(p.state(main).column, "mine")

    def test_reply_mark_with_several_sessions(self):
        p = load("reply_mark.py")
        q = Session("Claude Code", "idle", last_reply="❓ 確認: どちらにしますか")
        later = Session("Claude Code", "idle", last_reply="⏳ 途中: CI を待っている")
        waiting = Session("Claude Code", "waiting", "input needed")
        asking = "Claude Code が質問している: 確認: どちらにしますか"
        # 並びの順にかかわらず、答え待ち → ❓ → ⏳ の順に見る。❓ も答え待ちなので、ほかの会話が作業中でも先に出す
        for sessions in ((later, q), (q, later), (Session("Claude Code", "working"), q)):
            self.assertEqual((p.state(worktree(sessions=sessions)).column, p.state(worktree(sessions=sessions)).reason),
                             ("mine", asking), sessions)
        t = p.state(worktree(sessions=(later, waiting)))
        self.assertEqual((t.column, t.reason.key), ("mine", "reason.agent_waiting_for"))

    def test_issue_labels(self):
        p = load("issue_labels.py")
        self.assertEqual([c.id for c in p.columns], ["mine", "agent", "github", "review", "parked", "done"])
        issue = {"number": 12, "state": "OPEN", "labels": ["Blocked"]}
        self.assertEqual(p.state(worktree(issue=issue)).column, "parked")
        working = worktree(issue=issue, sessions=(Session("Claude Code", "working"),))
        self.assertEqual(p.state(working).column, "agent")
        closed = worktree(pr=None, issue={"number": 12, "state": "CLOSED", "labels": []})
        self.assertEqual(p.state(closed).column, "done")
        self.assertEqual(p.state(worktree()).column, "review")  # Issue が無ければ既定の判定のまま


if __name__ == "__main__":
    unittest.main()
