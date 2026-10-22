"""見本: Issue のラベルと状態で、止めている worktree を専用の列に分ける。

Issue に blocked や on hold のラベルを付けて作業を止めている人向け。Issue が閉じていて開いた PR が無ければ、完了に出す。
"""
from worktree_board.plugin import Column, Turn, default

NEEDS = ["issue"]  # GitHub から Issue を取る
HOLD = {"blocked", "on hold"}  # 止めている印のラベル（小文字で比べる）
COLUMNS = [*default.COLUMNS[:-1], Column("parked", "止めている"), "done"]


def state(worktree):
    t = default.state(worktree)
    issue = worktree.issue
    if t is None or issue is None:
        return t
    held = HOLD & {label.lower() for label in issue["labels"]}
    if held and t.column != "agent":  # エージェントが動いているなら、そちらを先に見せる
        return Turn("parked", f"Issue #{issue['number']} に {sorted(held)[0]} が付いている", None, t.badges)
    if issue["state"] == "CLOSED" and not worktree.pr:
        return Turn("done", f"Issue #{issue['number']} が閉じた", "worktree を片付ける", t.badges)
    return t
