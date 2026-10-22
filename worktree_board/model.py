"""状態を決める関数（プラグイン）が受け取る worktree と、返す答えの形。

どれもただのデータで、持っている間に手元の PC へ問い合わせない。その PC でしか取れない材料（worktree・会話）は、
集めるとき（collect.py）に入れておく。別の PC から届いた worktree も、同じ形で判定できるようにするため。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Session:
    """worktree の中で開いているエージェントの会話。status は working（作業中）・waiting（答え待ち）・idle（指示待ち）。"""
    agent: str
    status: str
    waiting_for: str | None = None  # waiting のとき、何を待っているか（エージェントが書いたまま）
    since: str | None = None  # 今の status になった時刻（ISO 8601）


@dataclass(frozen=True)
class Worktree:
    """関数が受け取る 1 つ。worktree・その branch・branch が出した PR・会話をまとめたもの。

    pr は開いている PR、closed は開いた PR が無いときの同じ branch の閉じた PR。どちらも `gh pr list --json` の 1 件のまま
    （pr の欄は sources.FIELDS、closed の欄は sources.CLOSED_FIELDS）。path が None なら、worktree の無い開いた PR。
    """
    host: str  # worktree がある PC の名前
    repo: str  # OWNER/REPO
    name: str  # 画面に出す名前（フォルダ名。worktree の無い PR は pr-<番号>）
    path: str | None
    branch: str | None  # detached の primary checkout なら None
    primary: bool = False  # primary checkout（git worktree list の先頭）か
    pr: dict | None = None
    closed: dict | None = None
    sessions: tuple[Session, ...] = ()
    me: str = ""  # GitHub のログイン名

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Worktree:
        return cls(**{**d, "sessions": tuple(Session(**s) for s in d.get("sessions") or ())})


@dataclass(frozen=True)
class Text:
    """翻訳のキー（locales/*.json）と引数。画面と端末が、見る人の言語に直す。"""
    key: str
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Turn:
    """関数の答え。column は列の id。reason・next・badges の 1 つずつは、そのまま出す文か Text。
    next が None なら、次にすることを出さない。"""
    column: str
    reason: str | Text
    next: str | Text | None = None
    badges: tuple[str | Text, ...] = ()


@dataclass(frozen=True)
class Column:
    """列。label が None なら、翻訳のキー state.<id> の文を出す。"""
    id: str
    label: str | None = None
