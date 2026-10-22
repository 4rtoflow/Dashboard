"""worktree と PR から、カードの状態を決める。副作用は無く、テストはここを見る。

状態は上から順に当てはめて決める（先に当たった規則が勝つ）。
  自分でしか動かせないもの（コンフリクト・CI の失敗・変更の要求）を先に、
  自動で進むもの（CI の実行中）を次に、そのあとに人を待つもの（Draft・レビュー）を見る。
文章は持たない。理由・次にすること・印は翻訳のキー（locales/*.json）と引数で返し、画面と CLI が自分の言語に直す。
"""
from __future__ import annotations

import re
from pathlib import Path

STATES = ["mine", "github", "review", "done"]  # 画面の列の順
FAILED = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "ERROR"}


def login(item: dict) -> str:
    return (item.get("author") or {}).get("login") or ""


def run_id(url: str | None) -> int:
    m = re.search(r"/actions/runs/(\d+)", url or "")
    return int(m.group(1)) if m else 0


def failed_checks(pr: dict) -> list[str]:
    """失敗しているチェックの名前。同じチェックは最新の run だけを見る（再実行で通ったものを数えない）。"""
    latest: dict[str, tuple[int, str | None]] = {}
    for c in pr.get("statusCheckRollup") or []:
        if c.get("__typename") == "StatusContext":
            latest[f"status/{c.get('context')}"] = (0, c.get("state"))
            continue
        key, rid = f"{c.get('workflowName') or ''}/{c.get('name')}", run_id(c.get("detailsUrl"))
        if key not in latest or rid >= latest[key][0]:
            latest[key] = (rid, c.get("conclusion"))
    return [k for k, (_, concl) in latest.items() if concl in FAILED]


def running_checks(pr: dict) -> list[str]:
    names = []
    for c in pr.get("statusCheckRollup") or []:
        if c.get("__typename") == "StatusContext":
            if c.get("state") in ("PENDING", "EXPECTED"):
                names.append(c.get("context") or "")
        elif c.get("status") != "COMPLETED":
            names.append(f"{c.get('workflowName') or ''}/{c.get('name')}")
    return names


def reviewers(pr: dict, me: str) -> dict[str, dict]:
    """自分以外のレビュアーごとの、最後の「承認・変更の要求・取り下げ」のレビュー。コメントだけのレビューは数えない。"""
    last: dict[str, dict] = {}
    for r in sorted(pr.get("reviews") or [], key=lambda r: r.get("submittedAt") or ""):
        if login(r) != me and r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            last[login(r)] = r
    return last


def change_requests(pr: dict, me: str) -> tuple[list[str], list[str]]:
    """変更を求めたままの人を、今の head に対して求めた人と、求めた後に push された人に分ける。"""
    on_head, pushed = [], []
    for who, r in sorted(reviewers(pr, me).items()):
        if r.get("state") == "CHANGES_REQUESTED":
            oid = (r.get("commit") or {}).get("oid")
            (on_head if oid in (None, pr.get("headRefOid")) else pushed).append(who)
    return on_head, pushed


def settle(prs: list[dict], memory: dict[int, str]) -> list[dict]:
    """mergeable は、GitHub が計算し直している間 UNKNOWN になる。その間は前回の値を使う（コンフリクトの表示が揺れない）。"""
    out = []
    for pr in prs:
        n, now = pr["number"], pr.get("mergeable") or "UNKNOWN"
        if now == "UNKNOWN":
            now = memory.get(n, "UNKNOWN")
        memory[n] = now
        out.append({**pr, "mergeable": now})
    for n in set(memory) - {pr["number"] for pr in prs}:
        del memory[n]
    return out


def turn(state: str, reason: str, next_step: str, **args) -> dict:
    """判定の結果。reason は {key, args}、next は翻訳のキー。"""
    return {"state": state, "reason": {"key": f"reason.{reason}", "args": args}, "next": f"next.{next_step}"}


def judge(pr: dict | None, closed: dict | None, me: str) -> dict:
    """1 つの worktree の状態。pr は開いている PR、closed は同じブランチの閉じた PR（どちらも無ければ PR がまだ無い）。"""
    if pr is None and closed:
        return turn("done", "merged" if closed.get("state") == "MERGED" else "closed", "cleanup", n=closed["number"])
    if pr is None:
        return turn("mine", "no_pr", "start")
    if pr.get("mergeable") == "CONFLICTING":
        return turn("mine", "conflict", "resolve")
    failed = failed_checks(pr)
    if failed:
        return turn("mine", "ci_failed", "fix_push", checks=failed[:2])
    asked, pushed = change_requests(pr, me)
    if asked:
        return turn("mine", "changes_requested", "address", who=asked)
    running = running_checks(pr)
    if running:
        return turn("github", "ci_running", "wait", checks=running[:2])
    if pr.get("isDraft"):
        return turn("mine", "draft", "ready")
    if pushed:
        return turn("review", "rereview", "wait", who=pushed)
    if any(r.get("state") == "APPROVED" for r in reviewers(pr, me).values()):
        return turn("mine", "approved", "merge")
    return turn("review", "review_waiting", "wait")


def badges(pr: dict | None, closed: dict | None, path: str | None) -> list[str]:
    out = []
    if pr and pr.get("isDraft"):
        out.append("badge.draft")
    if not pr and not closed:
        out.append("badge.no_pr")
    if not path:
        out.append("badge.no_worktree")
    return out


def sort_items(items: list[dict]) -> list[dict]:
    """同じ状態の中では、長く動いていないものを上に（動きの無い PR ほど気付きたい）。PR の無いものは最後。repo をまたいでも同じ順。"""
    return sorted(items, key=lambda i: (STATES.index(i["state"]), i["updated_at"] is None, i["updated_at"] or ""))


def build_items(worktrees: dict[str, str], prs: list[dict], closed: dict[str, dict], me: str,
                repo: str = "") -> list[dict]:
    """1 つの repo のカードの一覧。worktrees はブランチ名 → パス、closed はブランチ名 → 閉じた PR。
    worktree の無い開いた PR も 1 枚にする。repo はカードに付ける repo の名前。"""
    open_by_branch = {p["headRefName"]: p for p in prs}
    rows = [(Path(path).name, path, branch) for branch, path in worktrees.items()]
    rows += [(f"pr-{p['number']}", None, b) for b, p in open_by_branch.items() if b not in worktrees]
    items = []
    for name, path, branch in rows:
        pr = open_by_branch.get(branch)
        done = None if pr else closed.get(branch)
        shown = pr or done or {}
        items.append({
            **judge(pr, done, me),
            "id": f"{repo}:{name}", "repo": repo, "label": name if path else f"PR #{shown['number']}",
            "path": path, "branch": branch, "title": shown.get("title") or branch,
            "pr": shown.get("number"), "url": shown.get("url"), "updated_at": (pr or {}).get("updatedAt"),
            "badges": badges(pr, done, path),
        })
    return sort_items(items)
