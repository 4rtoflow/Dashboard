"""python3 -m worktree_board serve|status [--config PATH]"""
from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

from . import i18n


def print_errors(board, msgs: dict, file=sys.stdout) -> None:
    for e in board.errors():
        print(f"{e['repo']}: {i18n.describe_error(msgs, e['error'])}", file=file)


def status(board, msgs: dict) -> int:
    board.tick()
    print_errors(board, msgs)
    view = board.view()
    if not view["worktrees"] and not board.errors():
        print(i18n.fmt(msgs, "cli.empty"))
    several = len(view["repos"]) > 1  # repo が 1 つなら、行に repo の名前を付けない
    for col in view["columns"]:
        rows = [i for i in view["worktrees"] if i["state"] == col["id"]]
        if rows:
            name = col["label"] or i18n.fmt(msgs, f"state.{col['id']}")
            print(i18n.fmt(msgs, "ui.column_label", {"state": name, "n": len(rows)}))
            for i in rows:
                # 作業中・答え待ちは理由に出るので、印には返事を終えた（指示待ちの）会話だけを、エージェントごとに 1 つ足す
                idle = dict.fromkeys(a["agent"] for a in i["agents"] if a["status"] == "idle")
                chips = [i18n.say(msgs, b) for b in i["badges"]] + [i18n.fmt(msgs, "agent.idle", {"agent": a}) for a in idle]
                marks = f"  [{' / '.join(chips)}]" if chips else ""
                where = f"{i['repo']}  " if several else ""
                print(f"  {where}{i['label']}  {i18n.say(msgs, i['reason'])}{marks}")
    return 1 if board.errors() else 0


def main(argv: list[str]) -> int:
    msgs = i18n.catalog(i18n.detect())
    if sys.version_info < (3, 11):  # 設定ファイルの TOML を読む tomllib が 3.11 から
        print(i18n.fmt(msgs, "cli.python_too_old", {"version": platform.python_version()}), file=sys.stderr)
        return 1
    from . import config
    from .server import Board, serve
    from .sources import FetchError, discover

    t = lambda key, args=None: i18n.fmt(msgs, key, args).replace("%", "%%")  # argparse の書式として読まれないように
    p = argparse.ArgumentParser(prog="worktree_board", description=t("cli.description"))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=config.PATH, metavar="PATH",
                        help=t("cli.help_config", {"path": config.PATH}))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", parents=[common], help=t("cli.help_serve"))
    sub.add_parser("status", parents=[common], help=t("cli.help_status"))
    a = p.parse_args(argv)
    try:
        cfg = config.load(a.config)
    except config.ConfigError as e:
        print(i18n.fmt(msgs, e.key, e.params), file=sys.stderr)
        return 1
    msgs = i18n.catalog(i18n.normalize(cfg.lang) or i18n.detect())
    try:
        sources, skipped = discover(cfg.repos)
    except FetchError as e:  # git が無い
        print(i18n.describe_error(msgs, e.to_dict()), file=sys.stderr)
        return 1
    for path in skipped:
        print(i18n.fmt(msgs, "cli.skipped", {"path": path}), file=sys.stderr)
    if not sources:
        print(i18n.fmt(msgs, "cli.no_repos", {"path": cfg.path}), file=sys.stderr)
        return 1
    board = Board(sources, cfg.interval)
    if a.cmd == "status":
        return status(board, msgs)
    board.tick()  # 最初の 1 回は待たずに取り、repo や認証の誤りをすぐ知らせる
    print_errors(board, msgs, sys.stderr)
    if len(board.errors()) == len(sources):  # 1 つも取れなければ、見張っても何も出ない
        return 1
    return serve(board, cfg.port, msgs)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
