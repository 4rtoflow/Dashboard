"""worktree ごとに、その repo に結び付いた状態を決める関数を呼び、画面と端末に出すカードにする。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

from . import judge
from .model import Column, Text, Turn, Worktree

NEEDS = frozenset({"issue", "last_reply"})  # プラグインが頼める、時間のかかる材料


@dataclass(frozen=True)
class Plugin:
    """repo に結び付いた状態を決める関数と、その列の並び。origin は読み込んだファイル（既定の判定なら None）。
    needs は取ってほしい時間のかかる材料（NEEDS のうち）、issue_of は branch 名から Issue の番号を引く関数。"""
    state: Callable[[Worktree], Turn | None]
    columns: tuple[Column, ...]
    origin: str | None = None
    needs: frozenset[str] = frozenset()
    issue_of: Callable[[str | None], int | None] = judge.issue_of


DEFAULT = Plugin(judge.state, tuple(Column(c) for c in judge.COLUMNS))


def message(v) -> dict | None:
    """理由・次にすること・印の 1 つを、API で渡す形にする。翻訳のキーなら {key, args}、文なら {text}。"""
    if v is None:
        return None
    if isinstance(v, Text):
        return {"key": v.key, "args": v.args}
    return {"text": str(v)}


def failed(plugin: Plugin, detail: str) -> Turn:
    """関数が失敗したカード。ほかのカードは出し続けられるよう、先頭の列に理由を付けて出す。"""
    return Turn(plugin.columns[0].id, Text("reason.plugin_failed", {"detail": detail}))


def ask(plugin: Plugin, wt: Worktree) -> Turn | None:
    try:
        t = plugin.state(wt)
    except Exception as e:  # 自作の関数の誤りで、ほかの worktree まで止めない
        return failed(plugin, f"{type(e).__name__}: {e}")
    if t is None:
        return None
    if not isinstance(t, Turn):
        return failed(plugin, f"state() returned {type(t).__name__}, not Turn or None")
    if t.column not in {c.id for c in plugin.columns}:
        return failed(plugin, f"unknown column {t.column!r}")
    return t


def card(wt: Worktree, t: Turn) -> dict:
    shown = wt.pr or wt.closed or {}
    return {
        "id": f"{wt.host}:{wt.repo}:{wt.name}", "host": wt.host, "repo": wt.repo,
        "label": wt.name if wt.path else f"PR #{shown['number']}", "path": wt.path, "branch": wt.branch,
        "primary": wt.primary, "title": shown.get("title") or wt.branch or wt.name, "issue": wt.issue_number,
        "pr": shown.get("number"), "url": shown.get("url"), "updated_at": (wt.pr or {}).get("updatedAt"),
        "state": t.column, "reason": message(t.reason), "next": message(t.next),
        "badges": [message(b) for b in t.badges],
        "agents": [{k: v for k, v in asdict(s).items() if k != "last_reply"} for s in wt.sessions],  # 返事の全文は画面に要らない
    }


def build(plugin: Plugin, worktrees: list[Worktree]) -> list[dict]:
    """1 つの repo のカード。関数が None を返した worktree は出さない。"""
    return [card(wt, t) for wt in worktrees if (t := ask(plugin, wt)) is not None]


def merge_columns(plugins: list[Plugin]) -> list[Column]:
    """repo ごとの列の並びを、出てきた順に 1 つにまとめる。同じ id は先に出た列の名前を使う。"""
    out: dict[str, Column] = {}
    for p in plugins:
        for c in p.columns:
            out.setdefault(c.id, c)
    return list(out.values())


def sort(items: list[dict], columns: list[Column]) -> list[dict]:
    """列の順に、同じ列の中では長く動いていないものを上に（動きの無い PR ほど気付きたい）。PR の無いものは最後。"""
    order = {c.id: i for i, c in enumerate(columns)}
    return sorted(items, key=lambda i: (order[i["state"]], i["updated_at"] is None, i["updated_at"] or ""))
