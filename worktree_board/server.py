"""見張り（Board）と、Dashboard・API を出す HTTP サーバー。"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import agents, cards, collect, i18n
from .judge import settle
from .sources import FetchError

DASHBOARD = Path(__file__).with_name("dashboard.html")
WRITE_HEADER = "X-Dashboard"


class Watch:
    """1 つの repo の見張り。最後に取れた材料と、今の回のエラー（{key, args}。文章にするのは画面と CLI）を持つ。"""

    def __init__(self, source):
        self.source = source
        self.got: dict | None = None
        self.error: dict | None = None
        self.mergeable: dict[int, str] = {}

    def fetch(self) -> tuple[dict | None, dict | None]:
        """材料とエラーのどちらか。Issue の番号はいつも、Issue の中身はプラグインが頼んだときだけ足す。"""
        plugin = self.source.plugin or cards.DEFAULT
        try:
            got = self.source.fetch()
            numbers = collect.issue_numbers(plugin.issue_of, got)
            issues = self.source.issues(set(numbers.values())) if "issue" in plugin.needs else {}
            return {**got, "prs": settle(got["prs"], self.mergeable), "issue_numbers": numbers, "issues": issues}, None
        except FetchError as e:
            return None, e.to_dict()
        except json.JSONDecodeError:
            return None, {"key": "error.bad_json", "args": {}}
        except OSError as e:
            return None, {"key": "error.os", "args": {"detail": str(e)}}


class Board:
    """見直しのたびに、どの repo の材料も取り直す。取れなかった repo は、前の材料を残してエラーを出す。
    エージェントの会話は手元のファイルを読むだけなので、見直しを待たずに、カードを出すたびに読み直す。"""

    def __init__(self, sources, interval: int = 60, sessions=agents.sessions, host: str | None = None):
        self.watches = [Watch(s) for s in sources]
        self.interval = interval
        self.sessions = sessions
        self.host = host or collect.host_name()
        self.mu = threading.Lock()
        self.wake = threading.Event()
        self.updated_at: str | None = None  # どれかの repo が取れた最後の時刻
        self.checking = False

    def tick(self) -> None:
        self.checking = True
        try:
            # repo ごとの gh の待ちを重ねる（repo が増えても、見直しが repo の数の倍に延びない）
            with ThreadPoolExecutor(max_workers=max(1, min(4, len(self.watches)))) as pool:
                results = list(pool.map(Watch.fetch, self.watches))
        finally:
            self.checking = False
        with self.mu:
            for w, (got, error) in zip(self.watches, results, strict=True):
                if error is None:
                    w.got = got
                w.error = error
            if any(error is None for _, error in results):
                self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def errors(self) -> list[dict]:
        """今の回に取れなかった repo（{repo, error}）。"""
        with self.mu:
            return [{"repo": w.source.label, "error": w.error} for w in self.watches if w.error]

    def loop(self, first: bool = True) -> None:
        """見直しを繰り返す。first が False なら、最初の 1 回は済んでいるものとして間隔を待つ。"""
        while True:
            self.wake.clear()
            started = time.time()
            if first:
                self.tick()
            first = True
            # 眠っている間は単調時計が進まないので、壁時計で間隔を測る
            while (left := started + self.interval - time.time()) > 0 and not self.wake.wait(min(30, left)):
                pass

    def view(self) -> dict:
        """worktree を集め、repo ごとの状態を決める関数に聞いてカードにする。"""
        found = self.sessions()
        with self.mu:
            repos = [{"repo": w.source.label, "error": w.error} for w in self.watches]
            got = [(w.source.label, w.source.plugin or cards.DEFAULT, w.got) for w in self.watches if w.got]
            # 会話は、repo をまたいでいちばん深い worktree に振り分ける（repo の中に別の repo を置いていても二重にしない）
            paths = [p for _, _, g in got for p in [*g["worktrees"].values(), (g.get("primary") or {}).get("path")] if p]
            by_path = agents.assign(found, paths)
            items = [i for label, plugin, g in got
                     for i in cards.build(plugin, collect.local(label, g, by_path, self.host, plugin.needs))]
            columns = cards.merge_columns([w.source.plugin or cards.DEFAULT for w in self.watches])
            return {"repos": repos, "updated_at": self.updated_at, "checking": self.checking,
                    "columns": [{"id": c.id, "label": c.label} for c in columns], "worktrees": cards.sort(items, columns)}


class Handler(BaseHTTPRequestHandler):
    board: Board  # serve() で差し込む

    def log_message(self, *args) -> None:  # アクセスのたびにはログを書かない
        pass

    def reply(self, code: int, body, ctype: str = "application/json; charset=utf-8") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def route(self, method: str) -> None:
        # DNS を差し替えて、ほかのサイトの名前のまま届くのを防ぐ
        host = re.sub(r":\d+$", "", self.headers.get("Host", ""))
        if host not in ("localhost", "127.0.0.1"):
            return self.reply(403, {"error": "only localhost is accepted"})
        # 独自のヘッダーは、ほかのサイトのページからは付けて送れない（付けると事前確認で止まる）
        if method != "GET" and self.headers.get(WRITE_HEADER) != "1":
            return self.reply(403, {"error": f"missing {WRITE_HEADER} header"})
        path = urlsplit(self.path).path
        if method == "GET" and path == "/":
            return self.reply(200, DASHBOARD.read_bytes(), "text/html; charset=utf-8")
        if method == "GET" and path == "/api/worktrees":
            return self.reply(200, self.board.view())
        if method == "GET" and path == "/api/locales":
            return self.reply(200, i18n.languages())
        m = re.fullmatch(r"/locales/([A-Za-z-]+)\.json", path)
        if method == "GET" and m and m.group(1) in i18n.available():  # 一覧にある名前だけ読む（パスをそのまま使わない）
            return self.reply(200, (i18n.LOCALES / f"{m.group(1)}.json").read_bytes())
        if method == "POST" and path == "/api/refresh":
            self.board.wake.set()
            return self.reply(202, {"checking": True})
        return self.reply(404, {"error": "not found"})

    def do_GET(self) -> None:
        self.route("GET")

    def do_POST(self) -> None:
        self.route("POST")


def serve(board: Board, port: int, msgs: dict) -> int:
    """board は、最初の 1 回を見直し済みのもの。msgs は CLI の言語の翻訳。"""
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print(i18n.fmt(msgs, "cli.port_busy", {"port": port, "detail": e}))
        return 1
    Handler.board = board
    threading.Thread(target=board.loop, args=(False,), daemon=True).start()
    print(i18n.fmt(msgs, "cli.serving", {"port": port, "interval": board.interval}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
