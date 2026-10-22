import contextlib
import io
import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from worktree_board import i18n
from worktree_board.__main__ import main
from worktree_board.judge import STATES, build_items, change_requests, failed_checks, judge, running_checks, settle
from worktree_board.server import Board, Handler
from worktree_board.sources import FetchError, discover, parse_worktrees

ME = "me"


def pr(**kw) -> dict:
    return {"number": 1, "title": "t", "url": "u", "headRefName": "feat", "headRefOid": "h1", "isDraft": False,
            "mergeable": "MERGEABLE", "reviews": [], "reviewRequests": [], "statusCheckRollup": [], **kw}


def run(name, conclusion, rid, status="COMPLETED", wf="CI"):
    return {"__typename": "CheckRun", "name": name, "workflowName": wf, "status": status, "conclusion": conclusion,
            "detailsUrl": f"https://github.com/o/r/actions/runs/{rid}/job/1"}


def review(who, state, oid="h1", at="2026-01-01T00:00:00Z"):
    return {"author": {"login": who}, "state": state, "commit": {"oid": oid}, "submittedAt": at}


class JudgeOrder(unittest.TestCase):
    def state(self, p, closed=None):
        return judge(p, closed, ME)["state"]

    def test_no_pr(self):
        self.assertEqual(self.state(None), "mine")
        self.assertEqual(self.state(None, {"number": 3, "state": "MERGED"}), "done")

    def test_conflict_beats_running_ci(self):
        p = pr(mergeable="CONFLICTING", statusCheckRollup=[run("a", None, 1, "IN_PROGRESS")])
        self.assertEqual(self.state(p), "mine")

    def test_failed_ci_beats_running_ci(self):
        p = pr(statusCheckRollup=[run("a", "FAILURE", 1), run("b", None, 2, "IN_PROGRESS")])
        self.assertEqual(self.state(p), "mine")

    def test_running_ci_is_github_wait(self):
        self.assertEqual(self.state(pr(statusCheckRollup=[run("a", None, 1, "IN_PROGRESS")])), "github")

    def test_draft_waits_for_ci_first(self):
        # CI が走っている間は、Draft でも GitHub 待ち（終わってから Ready にする）
        p = pr(isDraft=True, statusCheckRollup=[run("a", None, 1, "QUEUED")])
        self.assertEqual(self.state(p), "github")
        self.assertEqual(self.state(pr(isDraft=True)), "mine")

    def test_changes_requested_on_head_is_mine(self):
        self.assertEqual(self.state(pr(reviews=[review("a", "CHANGES_REQUESTED")])), "mine")

    def test_push_after_change_request_waits_for_review(self):
        self.assertEqual(self.state(pr(reviews=[review("a", "CHANGES_REQUESTED", oid="old")])), "review")

    def test_approved_is_mine_unless_changes_requested(self):
        self.assertEqual(judge(pr(reviews=[review("a", "APPROVED")]), None, ME)["reason"]["key"], "reason.approved")
        both = pr(reviews=[review("a", "APPROVED"), review("b", "CHANGES_REQUESTED")])
        self.assertEqual(judge(both, None, ME)["reason"], {"key": "reason.changes_requested", "args": {"who": ["b"]}})

    def test_default_is_review_wait(self):
        self.assertEqual(self.state(pr()), "review")


class Checks(unittest.TestCase):
    def test_rerun_that_passed_is_not_failed(self):
        p = pr(statusCheckRollup=[run("a", "FAILURE", 1), run("a", "SUCCESS", 2)])
        self.assertEqual(failed_checks(p), [])
        # 実行順がどちらでも、最新の run（番号が大きいほう）で決まる
        p = pr(statusCheckRollup=[run("a", "SUCCESS", 2), run("a", "FAILURE", 1)])
        self.assertEqual(failed_checks(p), [])

    def test_status_context(self):
        p = pr(statusCheckRollup=[{"__typename": "StatusContext", "context": "ci/x", "state": "FAILURE"},
                                  {"__typename": "StatusContext", "context": "ci/y", "state": "PENDING"}])
        self.assertEqual(failed_checks(p), ["status/ci/x"])
        self.assertEqual(running_checks(p), ["ci/y"])


class Reviews(unittest.TestCase):
    def test_latest_review_per_person_counts(self):
        p = pr(reviews=[review("a", "CHANGES_REQUESTED", at="2026-01-01T00:00:00Z"),
                        review("a", "APPROVED", at="2026-01-02T00:00:00Z")])
        self.assertEqual(change_requests(p, ME), ([], []))

    def test_own_and_comment_only_reviews_are_ignored(self):
        p = pr(reviews=[review(ME, "CHANGES_REQUESTED"), review("a", "COMMENTED")])
        self.assertEqual(change_requests(p, ME), ([], []))


class Settle(unittest.TestCase):
    def test_unknown_keeps_last_value_and_forgets_closed_prs(self):
        memory: dict[int, str] = {}
        settle([pr(mergeable="CONFLICTING")], memory)
        self.assertEqual(settle([pr(mergeable="UNKNOWN")], memory)[0]["mergeable"], "CONFLICTING")
        settle([], memory)
        self.assertEqual(memory, {})


class Items(unittest.TestCase):
    def test_worktree_without_pr_and_pr_without_worktree(self):
        items = build_items({"wip": "/w/wip", "feat": "/w/feat"}, [pr()], {}, ME)
        by = {i["label"]: i for i in items}
        self.assertEqual(by["wip"]["badges"], ["badge.no_pr"])
        self.assertEqual(by["feat"]["state"], "review")
        items = build_items({}, [pr()], {}, ME)
        self.assertEqual((items[0]["label"], items[0]["badges"]), ("PR #1", ["badge.no_worktree"]))

    def test_closed_pr_only_counts_without_open_pr(self):
        closed = {"feat": {"number": 9, "state": "MERGED", "title": "old", "url": "u9"}}
        self.assertEqual(build_items({"feat": "/w/feat"}, [], closed, ME)[0]["state"], "done")
        self.assertEqual(build_items({"feat": "/w/feat"}, [pr()], closed, ME)[0]["state"], "review")

    def test_sorted_by_state_then_oldest(self):
        prs = [pr(number=1, headRefName="a", updatedAt="2026-01-03T00:00:00Z"),
               pr(number=2, headRefName="b", updatedAt="2026-01-01T00:00:00Z"),
               pr(number=3, headRefName="c", isDraft=True, updatedAt="2026-01-02T00:00:00Z")]
        items = build_items({}, prs, {}, ME)
        self.assertEqual([i["pr"] for i in items], [3, 2, 1])
        self.assertEqual(STATES.index(items[0]["state"]), 0)


class Worktrees(unittest.TestCase):
    def test_primary_first_and_detached_skipped(self):
        text = ("worktree /r\nHEAD aaa\nbranch refs/heads/main\n\n"
                "worktree /r-wt/x\nHEAD bbb\nbranch refs/heads/feat/x\n\n"
                "worktree /r-wt/d\nHEAD ccc\ndetached\n")
        self.assertEqual(parse_worktrees(text), ("/r", {"main": "/r", "feat/x": "/r-wt/x"}))


class FakeSource:
    def __init__(self, got=None, error=None, repo="o/r"):
        self.got, self.error, self.label = got, error, repo

    def fetch(self):
        if self.error:
            raise self.error
        return self.got


class BoardAndServer(unittest.TestCase):
    def test_failed_fetch_keeps_previous_items(self):
        src = FakeSource({"worktrees": {}, "prs": [pr()], "closed": {}, "me": ME})
        board = Board([src])
        board.tick()
        self.assertEqual(len(board.view()["worktrees"]), 1)
        src.error = FetchError("error.failed", detail="boom")
        board.tick()
        v = board.view()
        self.assertEqual(len(v["worktrees"]), 1)
        self.assertEqual(v["repos"], [{"repo": "o/r", "error": {"key": "error.failed", "args": {"detail": "boom"}}}])

    def test_repos_are_fetched_and_fail_separately(self):
        a = FakeSource({"worktrees": {}, "prs": [pr(updatedAt="2026-01-02T00:00:00Z")], "closed": {}, "me": ME}, repo="o/a")
        b = FakeSource({"worktrees": {}, "prs": [pr(updatedAt="2026-01-01T00:00:00Z")], "closed": {}, "me": ME}, repo="o/b")
        board = Board([a, b])
        board.tick()
        v = board.view()
        # 同じ PR 番号・同じブランチでも repo が違えば別のカード。repo をまたいで、長く動いていないものが上
        self.assertEqual([i["repo"] for i in v["worktrees"]], ["o/b", "o/a"])
        self.assertEqual(len({i["id"] for i in v["worktrees"]}), 2)
        stamp = v["updated_at"]
        b.error = FetchError("error.failed", detail="boom")
        board.tick()
        v = board.view()
        self.assertEqual([i["repo"] for i in v["worktrees"]], ["o/b", "o/a"])  # 取れなかった repo は前の材料のまま
        self.assertEqual([r["repo"] for r in v["repos"] if r["error"]], ["o/b"])
        self.assertEqual(board.errors(), [{"repo": "o/b", "error": {"key": "error.failed", "args": {"detail": "boom"}}}])
        self.assertGreaterEqual(v["updated_at"], stamp)  # 1 つでも取れていれば、見直しの時刻は進む
        a.error = FetchError("error.failed", detail="boom")
        board.tick()
        self.assertEqual(len(board.errors()), 2)

    def serve(self):
        board = Board([FakeSource({"worktrees": {}, "prs": [pr()], "closed": {}, "me": ME})])
        board.tick()
        Handler.board = board
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server.server_address[1]

    def request(self, port, path, method="GET", headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def test_api_and_guards(self):
        port = self.serve()
        code, body = self.request(port, "/api/worktrees")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["worktrees"][0]["pr"], 1)
        self.assertEqual(self.request(port, "/")[0], 200)
        # 翻訳は、一覧にある言語だけ配る（パスをそのまま開かない）
        self.assertEqual(self.request(port, "/locales/ja.json")[0], 200)
        self.assertEqual(self.request(port, "/locales/nope.json")[0], 404)
        self.assertEqual(self.request(port, "/locales/..%2Fjudge.json")[0], 404)
        self.assertEqual(len(json.loads(self.request(port, "/api/locales")[1])), len(i18n.available()))
        # ヘッダー無しの書き込みと、localhost 以外の名前の読み取りは断る
        self.assertEqual(self.request(port, "/api/refresh", "POST")[0], 403)
        self.assertEqual(self.request(port, "/api/refresh", "POST", {"X-Dashboard": "1"})[0], 202)
        self.assertEqual(self.request(port, "/api/worktrees", headers={"Host": "evil.example"})[0], 403)


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class Discover(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for name in ("a", "b"):
            git(self.root, "init", "-q", name)
            git(self.root / name, "-c", "user.name=t", "-c", "user.email=t@example.com",
                "commit", "-q", "--allow-empty", "-m", "init")
        git(self.root / "a", "worktree", "add", "-q", "-b", "feat", str(self.root / "a-feat"))
        (self.root / "notes").mkdir()
        (self.root / "README.md").write_text("x")

    def test_worktrees_of_one_repo_become_one_source_and_other_folders_are_skipped(self):
        # `--checkout root/*` を展開した形。a と a-feat は同じ repo。ファイルは知らせずに外す
        folders = sorted(self.root.iterdir()) + [self.root / "missing"]
        sources, skipped = discover(folders)
        self.assertEqual([s.checkout.name for s in sources], ["a", "b"])
        self.assertEqual([p.name for p in skipped], ["notes", "missing"])

    def run_main(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            return main(list(argv)), err.getvalue()

    def test_cli_refuses_repo_with_several_repos_and_stops_without_repos(self):
        code, err = self.run_main("--lang", "en", "status", "--checkout", str(self.root / "a"), str(self.root / "b"),
                                  "--repo", "o/r")
        self.assertEqual((code, err.strip()), (2, "--repo only works with a single repo"))
        code, err = self.run_main("--lang", "en", "status", "--checkout", str(self.root / "notes"))
        self.assertEqual(code, 1)
        self.assertIn("not a git repo", err)
        self.assertIn("No git repo to watch", err)


if __name__ == "__main__":
    unittest.main()
