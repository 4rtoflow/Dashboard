"""worktree を集める 1 か所。今は手元の PC だけを探す。別の PC から届いた一覧は、ここで足し合わせる。"""
from __future__ import annotations

import socket
from pathlib import Path

from . import agents
from .model import Session, Worktree
from .sources import FetchError


def host_name() -> str:
    """この PC の名前（ドメインを除いた部分）。worktree と、カードの id に入れる。"""
    return socket.gethostname().split(".")[0] or "localhost"


def branches(got: dict) -> set[str]:
    """材料に出てくる branch（worktree・primary checkout・開いた PR）。"""
    out = set(got["worktrees"]) | {p["headRefName"] for p in got["prs"]}
    return out | ({(got.get("primary") or {}).get("branch")} - {None})


def issue_numbers(issue_of, got: dict) -> dict[str, int | None]:
    """branch → Issue の番号。issue_of はプラグインの関数で、失敗や数以外の答えは、その repo の取得の誤りとして知らせる。"""
    out = {}
    for b in sorted(branches(got)):
        try:
            n = issue_of(b)
        except Exception as e:
            raise FetchError("error.plugin_issue_of", detail=f"{b}: {type(e).__name__}: {e}") from None
        if n is not None and (not isinstance(n, int) or isinstance(n, bool)):
            raise FetchError("error.plugin_issue_of", detail=f"{b}: returned {type(n).__name__}, not int or None")
        out[b] = n
    return out


def session(s: dict, needs) -> Session:
    reply = agents.last_reply(s) if "last_reply" in needs else None  # 会話の中身は、頼まれたときだけ読む
    return Session(s["agent"], s["status"], s.get("waiting_for"), s.get("since"), reply)


def local(repo: str, got: dict, by_path: dict[str, list[dict]], host: str, needs=frozenset()) -> list[Worktree]:
    """1 つの repo の、手元の worktree の一覧。got は Source.fetch に Issue の番号と中身を足したもの（server.Watch.fetch）、
    by_path はパス → 会話（agents.assign）、needs はプラグインが頼んだ材料。
    primary checkout と、worktree の無い開いた PR も入れる。出すかどうかは状態を決める関数が決める。"""
    numbers, issues = got.get("issue_numbers") or {}, got.get("issues") or {}
    open_by_branch = {p["headRefName"]: p for p in got["prs"]}
    primary = got.get("primary") or {}
    trees = [(branch, path, False) for branch, path in got["worktrees"].items()]
    if primary.get("path"):
        trees.append((primary.get("branch"), primary["path"], True))
    out = []
    for branch, path, main in trees:
        pr = open_by_branch.get(branch)
        out.append(Worktree(
            host=host, repo=repo, name=Path(path).name, path=path, branch=branch, primary=main,
            pr=pr, closed=None if pr else got["closed"].get(branch),
            sessions=tuple(session(s, needs) for s in by_path.get(path, [])),
            me=got["me"], issue_number=numbers.get(branch), issue=issues.get(numbers.get(branch))))
    taken = {branch for branch, _, _ in trees}
    out += [Worktree(host=host, repo=repo, name=f"pr-{p['number']}", path=None, branch=b, pr=p, me=got["me"],
                     issue_number=numbers.get(b), issue=issues.get(numbers.get(b)))
            for b, p in open_by_branch.items() if b not in taken]
    return out
