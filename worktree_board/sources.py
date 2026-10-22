"""git と gh から材料を取る。読むだけで、GitHub への発信はしない。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

FIELDS = ("number,title,url,headRefName,headRefOid,baseRefName,isDraft,mergeable,"
          "reviews,reviewRequests,statusCheckRollup,updatedAt")
CLOSED_FIELDS = "number,headRefName,state,title,url,mergedAt,closedAt"


class FetchError(Exception):
    """取れなかった理由。文章は持たず、翻訳のキー（error.*）と引数で持つ。"""

    def __init__(self, key: str, **args):
        super().__init__(key, args)
        self.key, self.params = key, args  # args は Exception の組み込みの名前で、代入するとタプルに直されてしまう

    def to_dict(self) -> dict:
        return {"key": self.key, "args": self.params}


def run(cmd: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise FetchError("error.command_missing", cmd=cmd[0]) from None
    except subprocess.TimeoutExpired:
        raise FetchError("error.timeout", cmd=" ".join(cmd[:3])) from None
    if r.returncode != 0:
        raise FetchError("error.failed", detail=r.stderr.strip()[:300] or " ".join(cmd[:3]))
    return r.stdout


def parse_worktrees(porcelain: str) -> tuple[str | None, dict[str, str]]:
    """`git worktree list --porcelain` の出力から、primary checkout のパスと、ブランチ名 → パス。
    先頭が primary checkout。ブランチを持たない（detached・bare）worktree は数えない。"""
    primary, out, path = None, {}, None
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            path = line[9:]
            primary = primary or path
        elif line.startswith("branch refs/heads/") and path:
            out[line[18:]] = path
    return primary, out


def common_dir(checkout: Path) -> Path | None:
    """checkout が属する repo の .git の場所（どの worktree から聞いても同じ）。git の repo でなければ None。"""
    if not checkout.is_dir():
        return None
    try:
        out = run(["git", "rev-parse", "--git-common-dir"], checkout).strip()
    except FetchError as e:
        if e.key != "error.failed":  # git が無いのは、repo でないのとは別に知らせる
            raise
        return None
    return (checkout / out).resolve()


def discover(checkouts: list[Path], repo: str | None = None) -> tuple[list[Source], list[Path]]:
    """見る repo の一覧と、git の repo でないので外したフォルダ。同じ repo の worktree をいくつ渡しても 1 つにまとめる
    （`--checkout ~/work/*` で primary checkout と worktree が並んでいても、カードが二重にならない）。
    `*` で混ざるファイルは、知らせずに外す。"""
    sources, seen, skipped = [], set(), []
    for checkout in checkouts:
        if checkout.is_file():
            continue
        key = common_dir(checkout)
        if key is None:
            skipped.append(checkout)
        elif key not in seen:
            seen.add(key)
            sources.append(Source(checkout, repo))
    return sources, skipped


class Source:
    """1 つの repo の材料の取り方。checkout はその repo のどこでもよい（worktree の中でも、primary checkout でも）。"""

    def __init__(self, checkout: Path, repo: str | None = None):
        self.checkout, self.repo, self.me = checkout, repo, None

    @property
    def label(self) -> str:
        """画面に出す repo の名前。gh に聞く前や、聞けなかったときはフォルダの名前。"""
        return self.repo or self.checkout.name

    def gh(self, *args: str) -> str:
        return run(["gh", *args], self.checkout)

    def fetch(self) -> dict:
        """worktrees（ブランチ名 → パス）・primary（primary checkout の path と branch）・prs（開いている自分の PR）・
        closed（ブランチ名 → 閉じた自分の PR）・me を返す。"""
        self.repo = self.repo or self.gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
        self.me = self.me or self.gh("api", "user", "--jq", ".login").strip()
        primary, branches = parse_worktrees(run(["git", "worktree", "list", "--porcelain"], self.checkout))
        worktrees = {b: p for b, p in branches.items() if p != primary}  # primary checkout は作業の場ではないので出さない
        prs = json.loads(self.gh("pr", "list", "--repo", self.repo, "--author", "@me", "--state", "open",
                                 "--limit", "100", "--json", FIELDS))
        rows = json.loads(self.gh("pr", "list", "--repo", self.repo, "--author", "@me", "--state", "closed",
                                  "--limit", "100", "--json", CLOSED_FIELDS))
        closed = {r["headRefName"]: r for r in sorted(rows, key=lambda r: r["number"])}  # 同じブランチは新しいほう
        main = next((b for b, p in branches.items() if p == primary), None)  # detached なら None
        return {"worktrees": worktrees, "primary": {"path": primary, "branch": main},
                "prs": prs, "closed": closed, "me": self.me}
