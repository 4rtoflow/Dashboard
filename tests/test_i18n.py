import json
import re
import unittest
from pathlib import Path

from worktree_board import cards, collect, i18n
from worktree_board.judge import COLUMNS, badges, judge
from worktree_board.model import Session, Worktree

ROOT = Path(__file__).resolve().parent.parent / "worktree_board"
EN = json.loads((ROOT / "locales" / "en.json").read_text(encoding="utf-8"))
HOLES = re.compile(r"\{(\w+)\}")


class Catalogs(unittest.TestCase):
    def test_twenty_one_languages(self):
        self.assertEqual(len(i18n.available()), 21)
        self.assertIn("tok", i18n.available())

    def test_every_language_has_exactly_the_english_keys(self):
        for code in i18n.available():
            data = json.loads((ROOT / "locales" / f"{code}.json").read_text(encoding="utf-8"))
            self.assertEqual(set(data), set(EN), code)
            self.assertIn(data["_dir"], ("ltr", "rtl"), code)

    def test_placeholders_match_english(self):
        # {名前} が欠ける・増える・綴りが違うと、画面に {n} がそのまま出る
        for code in i18n.available():
            for key, text in json.loads((ROOT / "locales" / f"{code}.json").read_text(encoding="utf-8")).items():
                self.assertEqual(set(HOLES.findall(text)), set(HOLES.findall(EN[key])), f"{code} {key}")

    def test_only_arabic_is_right_to_left(self):
        self.assertEqual({l["code"] for l in i18n.languages() if l["dir"] == "rtl"}, {"ar"})


class Keys(unittest.TestCase):
    def test_keys_the_judge_can_return_exist(self):
        # judge が返しうる理由・次にすること・印のキーを、全部の状況で集めて英語にあるか確かめる
        pr = {"number": 1, "headRefName": "b", "headRefOid": "h", "isDraft": False, "mergeable": "MERGEABLE",
              "reviews": [], "statusCheckRollup": []}
        cases = [None, {"state": "MERGED", "number": 1}, {"state": "CLOSED", "number": 1}]
        results = [judge(None, c, "me") for c in cases]
        for extra in ({"mergeable": "CONFLICTING"}, {"isDraft": True}, {},
                      {"statusCheckRollup": [{"__typename": "StatusContext", "context": "x", "state": "FAILURE"}]},
                      {"statusCheckRollup": [{"__typename": "StatusContext", "context": "x", "state": "PENDING"}]},
                      {"reviews": [{"author": {"login": "a"}, "state": "APPROVED", "submittedAt": "1"}]},
                      {"reviews": [{"author": {"login": "a"}, "state": "CHANGES_REQUESTED", "commit": {"oid": "h"}, "submittedAt": "1"}]},
                      {"reviews": [{"author": {"login": "a"}, "state": "CHANGES_REQUESTED", "commit": {"oid": "old"}, "submittedAt": "1"}]}):
            results.append(judge({**pr, **extra}, None, "me"))
        for status, what in (("waiting", None), ("waiting", "input needed"), ("working", None)):
            results.append(judge(None, None, "me", (Session("Claude Code", status, what),)))
        keys = {r.reason.key for r in results} | {r.next.key for r in results}
        wt = Worktree("h", "o/r", "b", None, "b")
        keys |= {b.key for b in badges(Worktree("h", "o/r", "b", None, "b", pr={**pr, "isDraft": True})) + badges(wt)}
        keys |= {f"state.{s}" for s in COLUMNS} | {cards.failed(cards.DEFAULT, "x").reason.key}
        self.assertEqual({k for k in keys if k not in EN}, set())
        self.assertGreaterEqual(len(keys), 20)  # 上の状況が全部、別々の結果を返している

    def test_every_key_used_by_the_page_exists(self):
        html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
        used = set(re.findall(r"data-i18n(?:-aria)?=\"([\w.]+)\"", html)) | set(re.findall(r"\bt\(['`]([\w.]+)['`]", html))
        self.assertEqual({k for k in used if k not in EN}, set())
        self.assertGreater(len(used), 10)

    def test_every_error_key_exists(self):
        src = "".join((ROOT / f).read_text(encoding="utf-8") for f in ("sources.py", "server.py", "config.py"))
        used = set(re.findall(r"\"(error\.\w+)\"", src))
        self.assertEqual({k for k in used if k not in EN}, set())
        self.assertGreater(len(used), 10)

    def test_every_cli_key_exists(self):
        src = (ROOT / "__main__.py").read_text(encoding="utf-8") + (ROOT / "server.py").read_text(encoding="utf-8")
        used = set(re.findall(r"\"(cli\.\w+)\"", src))
        self.assertEqual({k for k in used if k not in EN}, set())
        self.assertGreaterEqual(len(used), 10)  # 拾い損ねていないかの目安


class Formatting(unittest.TestCase):
    def test_lists_use_the_language_separator(self):
        ja, en = i18n.catalog("ja"), i18n.catalog("en")
        self.assertEqual(i18n.fmt(ja, "reason.ci_failed", {"checks": ["a", "b"]}), "CI が失敗: a、b")
        self.assertEqual(i18n.fmt(en, "reason.ci_failed", {"checks": ["a", "b"]}), "CI failed: a, b")

    def test_missing_argument_stays_visible_and_unknown_key_falls_back(self):
        en = i18n.catalog("en")
        self.assertEqual(i18n.fmt(en, "reason.merged", {}), "PR #{n} was merged")
        self.assertEqual(i18n.fmt(en, "no.such.key"), "no.such.key")

    def test_error_is_wrapped(self):
        self.assertEqual(i18n.describe_error(i18n.catalog("en"), {"key": "error.failed", "args": {"detail": "boom"}}),
                         "Could not fetch data: Command failed: boom")

    def test_detect_language(self):
        for tag, want in [("ja_JP.UTF-8", "ja"), ("zh_TW.UTF-8", "zh-Hant"), ("zh-HK", "zh-Hant"), ("zh_CN", "zh-Hans"),
                          ("zh-Hant-TW", "zh-Hant"), ("pt_PT", "pt"), ("tok", "tok"), ("AR", "ar"), ("de_DE@euro", "de")]:
            self.assertEqual(i18n.normalize(tag), want, tag)
        self.assertIsNone(i18n.normalize("xx_YY"))
        self.assertEqual(i18n.detect({"LC_ALL": "", "LANG": "ko_KR.UTF-8"}), "ko")
        self.assertEqual(i18n.detect({"LANG": "C"}), "en")
        self.assertEqual(i18n.detect({}), "en")

    def test_items_carry_no_prose(self):
        # 判定が文章を返すと、言語を切り替えても直らない。日本語・英語の文が入っていないことを見る
        pr = {"number": 1, "title": "t", "url": "u", "headRefName": "b", "headRefOid": "h", "isDraft": True, "mergeable": "MERGEABLE",
              "reviews": [], "statusCheckRollup": []}
        got = {"worktrees": {"b": "/w/b"}, "prs": [pr], "closed": {}, "me": "me"}
        item = cards.build(cards.DEFAULT, collect.local("o/r", got, {}, "h"))[0]
        self.assertTrue(item["reason"]["key"].startswith("reason."))
        self.assertTrue(item["next"]["key"].startswith("next."))
        self.assertNotIn("text", item["reason"])


if __name__ == "__main__":
    unittest.main()
