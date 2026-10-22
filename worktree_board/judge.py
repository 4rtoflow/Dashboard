"""既定の状態を決める関数（プラグインを書かない repo は、これで判定する）。副作用は無く、テストはここを見る。

状態は上から順に当てはめて決める（先に当たった規則が勝つ）。
  エージェントの会話の様子（人の答えを待っている・作業している）を PR より先に、
  自分でしか動かせないもの（コンフリクト・CI の失敗・変更の要求）を次に、
  自動で進むもの（CI の実行中）を次に、そのあとに人を待つもの（Draft・レビュー）を見る。
文章は持たない。理由・次にすること・印は翻訳のキー（locales/*.json）と引数で返し、画面と CLI が自分の言語に直す。
自作のプラグインからは `from worktree_board.plugin import default` で読み、`default.state(worktree)` で残りを任せられる。
"""
from __future__ import annotations

import re

from .model import Session, Text, Turn, Worktree

COLUMNS = ["mine", "agent", "github", "review", "done"]  # 画面の列の順
ISSUE_NUMBER = re.compile(r"(?:^|[/_#-])(\d+)(?=$|[/_-])")
FAILED = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "ERROR"}


def issue_of(branch: str | None) -> int | None:
    """branch 名の中の番号を Issue の番号とみなす（feature/5323-x・fix/12_y・5-z）。区切り（/ - _ #）に挟まれた数だけを見るので、
    release/2.1 や lodash-4.17.21 のような版の数は拾わない。"""
    m = ISSUE_NUMBER.search(branch or "")
    return int(m.group(1)) if m else None


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


def turn(column: str, reason: str, next_step: str, **args) -> Turn:
    return Turn(column, Text(f"reason.{reason}", args), Text(f"next.{next_step}"))


def agent_turn(sessions) -> Turn | None:
    """worktree の中のエージェントの会話から決まる状態。人の答えを待つ会話を、作業している会話より先に見る
    （同じ worktree でほかの会話が動いていても、答えるまで進まない）。どちらも無ければ None。"""
    for a in sessions:
        if a.status == "waiting":
            if a.waiting_for:
                return turn("mine", "agent_waiting_for", "answer_agent", agent=a.agent, what=a.waiting_for)
            return turn("mine", "agent_waiting", "answer_agent", agent=a.agent)
    for a in sessions:
        if a.status == "working":
            return turn("agent", "agent_working", "wait", agent=a.agent)
    return None


def judge(pr: dict | None, closed: dict | None, me: str, sessions: tuple[Session, ...] = ()) -> Turn:
    """1 つの worktree の状態。pr は開いている PR、closed は同じブランチの閉じた PR（どちらも無ければ PR がまだ無い）。
    sessions はその worktree で開いているエージェントの会話。PR がマージされた後でも、会話が答えを待っていれば先に出す。"""
    by_agent = agent_turn(sessions)
    if by_agent:
        return by_agent
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


def badges(wt: Worktree) -> tuple[Text, ...]:
    out = []
    if wt.pr and wt.pr.get("isDraft"):
        out.append(Text("badge.draft"))
    if not wt.pr and not wt.closed and not wt.primary:  # primary checkout は PR を作らずに使う場所なので、PR が無くても印を付けない
        out.append(Text("badge.no_pr"))
    if not wt.path:
        out.append(Text("badge.no_worktree"))
    return tuple(out)


def state(wt: Worktree) -> Turn | None:
    """既定の判定。primary checkout は、開いた PR があるか、エージェントが作業しているか答えを待っているときだけ出す
    （main で直接作業していても見落とさない）。次にすることは「自分の番」のときだけ出す。"""
    if wt.primary and not wt.pr and not agent_turn(wt.sessions):
        return None
    t = judge(wt.pr, wt.closed, wt.me, wt.sessions)
    return Turn(t.column, t.reason, t.next if t.column == "mine" else None, badges(wt))
