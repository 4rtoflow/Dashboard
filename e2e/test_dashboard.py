"""画面（dashboard.html）を本物のブラウザ（Playwright の Chromium）で開いて確かめる。

gh も git も呼ばず、決めた材料から 5 つの列に 1 枚ずつカードを作るサーバーを立てる。
Playwright が要るので tests/ とは分けている。流し方は AGENTS.md の「3. テンプレートを直す」。
"""
from __future__ import annotations

import os
import re
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    if os.environ.get("CI"):  # CI では、入れ損ねたまま飛ばして通さない
        raise
    raise unittest.SkipTest("Playwright が無い（pip install playwright && playwright install chromium）") from None

from worktree_board import i18n
from worktree_board.server import Board, Handler
from worktree_board.sources import FetchError

EN = i18n.catalog("en")
LANGS = [lang["code"] for lang in i18n.languages()]
EXPECTED = {"mine": "conflict", "agent": "agent", "github": "ci", "review": "review", "done": "merged"}  # 列 → そこに出る worktree
XSS = '<img src="/x.png" onerror="window.xss = 1">'  # カードの題は、HTML として読まずに文字のまま出す
RAW = re.compile(r"\{\w+\}|\b(?:ui|state|reason|next|badge|agent|error)\.[a-z_]+\b")  # 埋まっていない {名前} と、翻訳のキーのまま
LIGHT, DARK = "rgb(255, 255, 255)", "rgb(26, 26, 26)"  # 明るい表示と暗い表示の地の色（--bg）


def pr(number, branch, **kw) -> dict:
    return {"number": number, "title": f"PR {number}", "url": f"https://github.com/o/app/pull/{number}", "headRefName": branch,
            "headRefOid": "h1", "isDraft": False, "mergeable": "MERGEABLE", "reviews": [], "reviewRequests": [],
            "statusCheckRollup": [], "updatedAt": "2026-01-01T00:00:00Z", **kw}


class App:
    """カードが 5 つの列に 1 枚ずつ出る repo。"""
    label, plugin = "o/app", None

    def fetch(self) -> dict:
        running = {"__typename": "CheckRun", "name": "test", "workflowName": "CI", "status": "IN_PROGRESS", "conclusion": None,
                   "detailsUrl": "https://github.com/o/app/actions/runs/1/job/1"}
        return {"worktrees": {b: f"/w/{b}" for b in EXPECTED.values()}, "primary": {"path": "/w/app", "branch": "main"},
                "prs": [pr(1, "conflict", mergeable="CONFLICTING"), pr(2, "agent"), pr(3, "ci", statusCheckRollup=[running]),
                        pr(4, "review", title=XSS)],
                "closed": {"merged": {"number": 5, "state": "MERGED", "title": "PR 5", "url": "https://github.com/o/app/pull/5"}},
                "me": "me"}


class Broken:
    """取れない repo。ほかの repo のカードはそのまま出し、上に 1 行の知らせを出す。"""
    label, plugin = "o/broken", None

    def fetch(self) -> dict:
        raise FetchError("error.failed", detail="boom")


def sessions() -> list[dict]:
    return [{"agent": "Claude Code", "status": "working", "waiting_for": None, "since": None, "cwd": "/w/agent", "transcript": None}]


class OtherSite(BaseHTTPRequestHandler):
    """ほかのサイトのページ。ここから Dashboard の API を使えないことを確かめる。"""

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        body = b"<!doctype html><title>other</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start(handler) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


class Dashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.board = Board([App(), Broken()], sessions=sessions, host="h")
        cls.board.tick()
        cls.view = cls.board.view()
        Handler.board = cls.board
        (cls.server, cls.url), (cls.other, cls.other_url) = start(Handler), start(OtherSite)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for server in (cls.server, cls.other):
            server.shutdown()
            server.server_close()

    def setUp(self):
        self.board.wake.clear()
        self.contexts, self.outside, self.errors = [], [], []
        self.allowed = None  # わざと失敗させる要求のある確かめでは、そのコンソールのエラーを許す

    def tearDown(self):
        for c in self.contexts:
            c.close()
        self.assertEqual(self.outside, [], "外のサイトへ要求した")
        self.assertEqual([e for e in self.errors if not (self.allowed and self.allowed.search(e))], [], "コンソールにエラーが出た")

    def new_page(self, **options):
        context = self.browser.new_context(**{"locale": "en-US", "color_scheme": "light", **options})
        self.contexts.append(context)
        # 要求は route で横取りせずに見るだけにする（横取りすると、Playwright が CORS の事前確認に自分で答えてしまう）
        context.on("request", self.only_local)
        page = context.new_page()
        page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        return page

    def only_local(self, request) -> None:
        if not request.url.startswith((self.url + "/", self.other_url + "/")):
            self.outside.append(request.url)

    def open(self, **options):
        page = self.new_page(**options)
        page.goto(self.url)
        page.wait_for_selector(".card")
        return page

    def choose(self, page, code: str) -> None:
        page.select_option("#lang", code)
        page.wait_for_function("code => document.documentElement.lang === code", arg=code)

    def test_cards_land_in_their_columns(self):
        self.assertEqual({i["label"]: i["state"] for i in self.view["worktrees"]}, {v: k for k, v in EXPECTED.items()})
        page = self.open()
        self.assertEqual(page.eval_on_selector_all("main > section", "s => s.map(e => e.classList[1])"), list(EXPECTED))
        for col, label in EXPECTED.items():
            self.assertEqual(page.locator(f"section.{col} .card .label").all_inner_texts(), [label], col)
            self.assertEqual(page.locator(f"section.{col} h2 .count").inner_text(), "1", col)
        self.assertEqual(page.title(), "(1) worktree board")
        # PR のあるカードは、その PR を新しいタブで開く
        card = page.locator("section.mine .card")
        self.assertEqual((card.get_attribute("href"), card.get_attribute("target")), ("https://github.com/o/app/pull/1", "_blank"))
        # 題は HTML として読まない
        self.assertEqual(page.locator("section.review .card .title").inner_text(), XSS)
        self.assertIsNone(page.evaluate("window.xss"))
        # 取れなかった repo は 1 行で知らせ、ほかの repo のカードは出したまま
        error = next(r["error"] for r in self.view["repos"] if r["error"])
        self.assertEqual(page.locator("#alert").inner_text(), f"o/broken: {i18n.describe_error(EN, error)}")
        self.assertEqual(page.locator("#repo").inner_text(), "o/app · o/broken")

    def test_every_language_fills_every_text(self):
        page = self.open()
        for code in LANGS:
            self.choose(page, code)
            msgs = i18n.catalog(code)
            self.assertEqual(page.evaluate("document.documentElement.dir"), msgs["_dir"], code)
            self.assertEqual(page.locator("#refresh").inner_text(), msgs["ui.refresh"], code)
            for col in EXPECTED:
                label = i18n.fmt(msgs, "ui.column_label", {"state": msgs[f"state.{col}"], "n": 1})
                self.assertEqual(page.locator(f"section.{col}").get_attribute("aria-label"), label, code)
            # 理由と次にすることは、サーバーの i18n と同じ文になる
            for item in self.view["worktrees"]:
                card = page.locator(".card", has=page.locator(".label", has_text=re.compile(f"^{item['label']}$")))
                self.assertEqual(card.locator(".reason").inner_text(), i18n.say(msgs, item["reason"]), f"{code} {item['label']}")
                if item["next"]:
                    want = i18n.fmt(msgs, "ui.next", {"step": i18n.say(msgs, item["next"])})
                    self.assertEqual(card.locator(".next").inner_text(), want, f"{code} {item['label']}")
            text = page.inner_text("body").replace(XSS, "")
            self.assertEqual(RAW.findall(text), [], code)

    def test_language_follows_the_browser_until_chosen(self):
        for locale, code in (("ja-JP", "ja"), ("zh-TW", "zh-Hant"), ("pt-BR", "pt"), ("ar-EG", "ar"), ("xx-YY", "en")):
            page = self.open(locale=locale)
            self.assertEqual(page.evaluate("document.documentElement.lang"), code, locale)
        self.choose(page, "de")
        page.reload()
        page.wait_for_selector(".card")
        self.assertEqual(page.evaluate("document.documentElement.lang"), "de")

    def test_theme_is_chosen_and_remembered(self):
        page = self.open()
        bg = lambda: page.evaluate("getComputedStyle(document.body).backgroundColor")
        theme = lambda: page.evaluate("document.documentElement.dataset.theme ?? null")
        pressed = lambda: page.eval_on_selector_all("[data-scheme]", "bs => bs.filter(b => b.ariaPressed === 'true').map(b => b.dataset.scheme)")
        self.assertEqual((bg(), theme(), pressed()), (LIGHT, None, ["auto"]))
        page.click('[data-scheme="dark"]')
        self.assertEqual((bg(), theme(), pressed()), (DARK, "dark", ["dark"]))
        page.reload()
        page.wait_for_selector(".card")
        self.assertEqual((bg(), theme(), pressed()), (DARK, "dark", ["dark"]))
        page.click('[data-scheme="light"]')
        page.emulate_media(color_scheme="dark")
        self.assertEqual((bg(), theme()), (LIGHT, "light"))
        # 自動に戻すと、OS の設定に合わせる
        page.click('[data-scheme="auto"]')
        self.assertEqual((bg(), theme(), pressed()), (DARK, None, ["auto"]))
        page.emulate_media(color_scheme="light")
        self.assertEqual(bg(), LIGHT)

    def test_phone_width_has_no_sideways_scroll(self):
        page = self.open(viewport={"width": 360, "height": 740})
        for code in LANGS:
            self.choose(page, code)
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 360, code)

    def test_refresh_asks_the_server(self):
        page = self.open()
        with page.expect_response(lambda r: r.url.endswith("/api/refresh")) as got:
            page.click("#refresh")
        r = got.value
        self.assertEqual((r.request.method, r.request.headers.get("x-dashboard"), r.status), ("POST", "1", 202))
        self.assertTrue(self.board.wake.is_set())
        # 見直しが終わるまで、ボタンは押せない
        self.assertTrue(page.locator("#refresh").is_disabled())
        self.assertEqual(page.locator("#refresh").inner_text(), EN["ui.refreshing"])

    def test_old_update_turns_red_and_lost_server_is_told(self):
        page = self.new_page()

        def old(route):
            r = route.fetch()
            route.fulfill(response=r, json={**r.json(), "updated_at": "2020-01-01T00:00:00+00:00"})

        page.route("**/api/worktrees", old)
        page.goto(self.url)
        page.wait_for_selector(".card")
        self.assertEqual(page.get_attribute("#stamp", "class"), "stamp stale")
        stale = re.escape(EN["ui.updated_stale"]).replace(r"\{time\}", ".+").replace(r"\{min\}", "5")
        self.assertRegex(page.inner_text("#stamp"), f"^{stale}$")
        page.unroute("**/api/worktrees")
        self.allowed = re.compile(r"Failed to load resource")
        page.route("**/api/worktrees", lambda route: route.abort())
        page.reload()
        page.wait_for_function("t => document.getElementById('alert').textContent === t", arg=EN["ui.server_unreachable"])
        self.assertTrue(page.is_visible("#alert"))

    def test_other_sites_cannot_use_the_api(self):
        page = self.new_page()
        page.goto(self.other_url)
        self.allowed = re.compile(r"CORS policy|Failed to load resource")
        got = page.evaluate("""async url => {
          const tryIt = async f => { try { return await f(); } catch { return 'blocked'; } };
          return {
            // 独自のヘッダーを付けると、事前確認（OPTIONS）で止まる
            withHeader: await tryIt(async () => (await fetch(url + '/api/refresh', {method: 'POST', headers: {'X-Dashboard': '1'}})).status),
            // ヘッダー無しなら届くが、サーバーが断る（中身は読めない）
            noHeader: await tryIt(async () => (await fetch(url + '/api/refresh', {method: 'POST', mode: 'no-cors'})).type),
            read: await tryIt(async () => (await (await fetch(url + '/api/worktrees')).json()).worktrees.length),
          };
        }""", self.url)
        self.assertEqual(got, {"withHeader": "blocked", "noHeader": "opaque", "read": "blocked"})
        self.assertFalse(self.board.wake.is_set())


if __name__ == "__main__":
    unittest.main()
