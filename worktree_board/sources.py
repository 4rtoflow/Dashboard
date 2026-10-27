"""git と gh から材料を取る。読むだけで、GitHub への発信はしない。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

FIELDS = ("number,title,url,headRefName,headRefOid,baseRefName,isDraft,mergeable,"
          "reviews,reviewRequests,statusCheckRollup,updatedAt")
CLOSED_FIELDS = "number,headRefName,state,title,url,mergedAt,closedAt"
ISSUE_FIELDS = "number title state stateReason url updatedAt labels(first: 20) { nodes { name } } assignees(first: 10) { nodes { login } }"
ISSUES_PER_QUERY = 50


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


def graphql(query: str, cwd: Path) -> dict:
    """gh api graphql の data。番号の Issue が無い（NOT_FOUND）だけの誤りなら、取れた分を返す（gh はそのとき終了コード 1 になる）。"""
    try:
        r = subprocess.run(["gh", "api", "graphql", "-f", f"query={query}"], cwd=cwd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise FetchError("error.command_missing", cmd="gh") from None
    except subprocess.TimeoutExpired:
        raise FetchError("error.timeout", cmd="gh api graphql") from None
    try:
        body = json.loads(r.stdout or "{}")
    except ValueError:
        body = {}
    errors = body.get("errors") or []
    if body.get("data") is not None and all(e.get("type") == "NOT_FOUND" for e in errors):
        return body["data"]
    raise FetchError("error.failed", detail=(r.stderr.strip() or str(errors))[:300] or "gh api graphql")


def issue_record(node: dict) -> dict:
    """GraphQL の Issue を、プラグインに渡す形にする（ラベルは名前、担当はログイン名の並び）。"""
    return {**{k: node.get(k) for k in ("number", "title", "state", "stateReason", "url", "updatedAt")},
            "labels": [n["name"] for n in (node.get("labels") or {}).get("nodes") or []],
            "assignees": [n["login"] for n in (node.get("assignees") or {}).get("nodes") or []]}


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


def discover(repos) -> tuple[list[Source], list[Path]]:
    """設定ファイルの repo（config.Repo）から、見る repo の一覧と、git の repo でないので外したパスを返す。
    同じ repo の worktree を何度書いても 1 つにまとめる（先に書いたほうの名前と関数を使う）。"""
    sources, seen, skipped = [], set(), []
    for r in repos:
        key = common_dir(r.path)
        if key is None:
            skipped.append(r.path)
        elif key not in seen:
            seen.add(key)
            sources.append(Source(r.path, r.name, r.plugin))
    return sources, skipped


class Source:
    """1 つの repo の材料の取り方。checkout はその repo のどこでもよい（worktree の中でも、primary checkout でも）。
    plugin はその repo の状態を決める関数（cards.Plugin）。"""

    def __init__(self, checkout: Path, repo: str | None = None, plugin=None):
        self.checkout, self.repo, self.plugin = checkout, repo, plugin
        self.me: str | None = None

    @property
    def label(self) -> str:
        """画面に出す repo の名前。gh に聞く前や、聞けなかったときはフォルダの名前。"""
        return self.repo or self.checkout.name

    def gh(self, *args: str) -> str:
        return run(["gh", *args], self.checkout)

    def issues(self, numbers) -> dict[int, dict]:
        """番号 → Issue。番号が PR だったり、無かったりすれば入れない。ISSUES_PER_QUERY 件ずつ 1 回の問い合わせで取る。"""
        assert self.repo, "fetch() が先に repo を決める"
        owner, name = self.repo.split("/", 1)
        out: dict[int, dict] = {}
        wanted = sorted(n for n in numbers if isinstance(n, int) and not isinstance(n, bool) and n > 0)
        for i in range(0, len(wanted), ISSUES_PER_QUERY):
            fields = " ".join(f"i{n}: issueOrPullRequest(number: {n}) {{ __typename ... on Issue {{ {ISSUE_FIELDS} }} }}"
                              for n in wanted[i:i + ISSUES_PER_QUERY])
            repo = graphql(f"query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {fields} }} }}",
                           self.checkout).get("repository") or {}
            out.update({node["number"]: issue_record(node) for node in repo.values() if node and node.get("__typename") == "Issue"})
        return out

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
