"""python3 -m worktree_board serve|status"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import i18n
from .judge import STATES
from .server import Board, serve
from .sources import FetchError, discover


def print_errors(board: Board, msgs: dict, file=sys.stdout) -> None:
    for e in board.errors():
        print(f"{e['repo']}: {i18n.describe_error(msgs, e['error'])}", file=file)


def status(board: Board, msgs: dict) -> int:
    board.tick()
    print_errors(board, msgs)
    view = board.view()
    if not view["worktrees"] and not board.errors():
        print(i18n.fmt(msgs, "cli.empty"))
    several = len(view["repos"]) > 1  # repo が 1 つなら、行に repo の名前を付けない
    for state in STATES:
        rows = [i for i in view["worktrees"] if i["state"] == state]
        if rows:
            print(i18n.fmt(msgs, "ui.column_label", {"state": i18n.fmt(msgs, f"state.{state}"), "n": len(rows)}))
            for i in rows:
                marks = f"  [{' / '.join(i18n.fmt(msgs, b) for b in i['badges'])}]" if i["badges"] else ""
                where = f"{i['repo']}  " if several else ""
                print(f"  {where}{i['label']}  {i18n.fmt(msgs, i['reason']['key'], i['reason']['args'])}{marks}")
    return 1 if board.errors() else 0


def pick_language(argv: list[str]) -> str:
    """--lang があればそれ、無ければ環境変数から。--help の文章も選んだ言語で出すので、本体の解析より先に決める。"""
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--lang")
    known, _ = pre.parse_known_args(argv)
    return i18n.normalize(known.lang) or i18n.detect()


def main(argv: list[str]) -> int:
    msgs = i18n.catalog(pick_language(argv))
    t = lambda key: msgs[key].replace("%", "%%")  # argparse の書式として読まれないように
    p = argparse.ArgumentParser(prog="worktree_board", description=t("cli.description"))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", help=t("cli.help_repo"))
    # 何度でも、1 回に何個でも渡せる（`--checkout ~/work/*` をシェルが展開したものも受ける）。
    # 既定を [] にすると extend がそこへ足し込むので、None にしておいて後で今のフォルダにする
    common.add_argument("--checkout", type=Path, nargs="+", action="extend", metavar="PATH", help=t("cli.help_checkout"))
    lang_help = t("cli.help_lang") + f" [{', '.join(i18n.available())}]"
    common.add_argument("--lang", default=argparse.SUPPRESS, help=lang_help)  # サブコマンドの後ろでも、前でも置ける
    p.add_argument("--lang", help=lang_help)
    sub = p.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("serve", parents=[common], help=t("cli.help_serve"))
    ps.add_argument("--port", type=int, default=8765, help=t("cli.help_port"))
    ps.add_argument("--interval", type=int, default=60, help=t("cli.help_interval"))
    sub.add_parser("status", parents=[common], help=t("cli.help_status"))
    a = p.parse_args(argv)
    try:
        sources, skipped = discover([c.expanduser().resolve() for c in a.checkout or [Path.cwd()]], a.repo)
    except FetchError as e:  # git が無い
        print(i18n.describe_error(msgs, e.to_dict()), file=sys.stderr)
        return 1
    for path in skipped:
        print(i18n.fmt(msgs, "cli.skipped", {"path": path}), file=sys.stderr)
    if not sources:
        print(i18n.fmt(msgs, "cli.no_repos"), file=sys.stderr)
        return 1
    if a.repo and len(sources) > 1:  # どの repo に当てる名前か決められない
        print(i18n.fmt(msgs, "cli.repo_needs_one_checkout"), file=sys.stderr)
        return 2
    board = Board(sources, getattr(a, "interval", 60))
    if a.cmd == "status":
        return status(board, msgs)
    board.tick()  # 最初の 1 回は待たずに取り、repo や認証の誤りをすぐ知らせる
    print_errors(board, msgs, sys.stderr)
    if len(board.errors()) == len(sources):  # 1 つも取れなければ、見張っても何も出ない
        return 1
    return serve(board, a.port, msgs)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
