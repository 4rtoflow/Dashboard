"""worktree を集める 1 か所。今は手元の PC だけを探す。別の PC から届いた一覧は、ここで足し合わせる。"""
from __future__ import annotations

import socket
from pathlib import Path

from .model import Session, Worktree


def host_name() -> str:
    """この PC の名前（ドメインを除いた部分）。worktree と、カードの id に入れる。"""
    return socket.gethostname().split(".")[0] or "localhost"


def local(repo: str, got: dict, by_path: dict[str, list[dict]], host: str) -> list[Worktree]:
    """1 つの repo の、手元の worktree の一覧。got は sources.Source.fetch の結果、by_path はパス → 会話（agents.assign）。
    primary checkout と、worktree の無い開いた PR も入れる。出すかどうかは状態を決める関数が決める。"""
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
            sessions=tuple(Session(s["agent"], s["status"], s.get("waiting_for"), s.get("since")) for s in by_path.get(path, [])),
            me=got["me"]))
    taken = {branch for branch, _, _ in trees}
    out += [Worktree(host=host, repo=repo, name=f"pr-{p['number']}", path=None, branch=b, pr=p, me=got["me"])
            for b, p in open_by_branch.items() if b not in taken]
    return out
