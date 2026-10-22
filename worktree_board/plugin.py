"""状態を決める関数（プラグイン）を書く人向けの窓口。プラグインからは、ここにあるものだけを使う
（ほかのモジュールは、テンプレートを直すと変わりうる）。

設定ファイルの [[repos]] に plugin = "<ファイルのパス>" を書くと、その repo の worktree ごとに state(worktree) が呼ばれる。

    from worktree_board.plugin import Turn, default

    COLUMNS = default.COLUMNS  # 列の id の並び（Column で名前も付けられる）。書かなければ既定の列

    def state(worktree):
        if worktree.pr and worktree.pr["title"].startswith("WIP"):
            return Turn("mine", "WIP のまま", "仕上げて WIP を外す")
        return default.state(worktree)  # 残りは既定の判定に任せる

- state は Worktree を 1 つ受け取り、Turn か None（カードを出さない）を返す。
- Turn の column は COLUMNS のどれか。理由・次にすること・印は、文をそのまま書いてよい（翻訳のキーにするなら Text）。
- 例外を投げたり、COLUMNS に無い列を返したりすると、そのカードだけを先頭の列に「関数が失敗した」と出し、ほかは出し続ける。
- 呼ばれるのは、画面が読み直すたび（10 秒おき）。重い処理や GitHub への書き込みはしない。
- 同じフォルダのファイルは import できる。直したら、起動し直すと反映される。
"""
from . import judge as default
from .model import Column, Session, Text, Turn, Worktree

__all__ = ["Column", "Session", "Text", "Turn", "Worktree", "default"]
