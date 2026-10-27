import tempfile
import unittest
from pathlib import Path

from worktree_board import cards, collect, config
from worktree_board.model import Column, Session, Text, Turn, Worktree
from worktree_board.server import Board

ME = "me"


def pr(**kw) -> dict:
    return {"number": 1, "title": "t", "url": "u", "headRefName": "feat", "headRefOid": "h1", "isDraft": False,
            "mergeable": "MERGEABLE", "reviews": [], "reviewRequests": [], "statusCheckRollup": [], **kw}


class Files(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def write(self, name, text):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return p

    def error(self, text, name="config.toml"):
        with self.assertRaises(config.ConfigError) as e:
            config.load(self.write(name, text))
        return e.exception.key, e.exception.params


class Load(Files):
    def test_defaults_and_paths(self):
        cfg = config.load(self.write("config.toml", '[[repos]]\npath = "work/a"\n\n[[repos]]\npath = "~/b"\nname = "o/b"\n'))
        self.assertEqual((cfg.port, cfg.interval, cfg.lang), (8765, 60, None))
        a, b = cfg.repos
        self.assertEqual(a.path, self.root / "work/a")  # 相対パスは設定ファイルのフォルダから
        self.assertEqual((b.path, b.name), (Path.home() / "b", "o/b"))
        self.assertIs(a.plugin, cards.DEFAULT)

    def test_missing_file_points_to_the_example(self):
        with self.assertRaises(config.ConfigError) as e:
            config.load(self.root / "none.toml")
        self.assertEqual(e.exception.key, "error.config_missing")
        self.assertTrue(Path(e.exception.params["example"]).is_file())

    def test_errors_say_where(self):
        key, args = self.error('port = 8765\n[[repos]\npath = "a"\n')
        self.assertEqual(key, "error.config_syntax")
        self.assertIn("line 2", args["detail"])
        self.assertEqual(self.error('colour = "red"\n[[repos]]\npath = "a"\n'), ("error.config_key", {"path": str(self.root / "config.toml"), "key": "colour"}))
        self.assertEqual(self.error('[[repos]]\npath = "a"\nplugn = "p.py"\n')[1]["key"], "repos[1].plugn")
        self.assertEqual(self.error('port = "8765"\n[[repos]]\npath = "a"\n')[1]["expected"], "integer")
        self.assertEqual(self.error('port = true\n[[repos]]\npath = "a"\n')[0], "error.config_value")  # bool は数に数えない
        self.assertEqual(self.error('port = 1\n')[0], "error.config_no_repos")
        self.assertEqual(self.error('[[repos]]\nname = "o/r"\n'), ("error.config_required", {"path": str(self.root / "config.toml"), "key": "repos[1].path"}))


class Plugins(Files):
    def plugin(self, body, name="p.py"):
        self.write(name, body)
        return config.load(self.write("config.toml", f'[[repos]]\npath = "a"\nplugin = "{name}"\n')).repos[0].plugin

    def test_plugin_can_import_the_api_and_its_neighbours(self):
        self.write("helpers.py", "PREFIX = 'WIP'\n")
        plugin = self.plugin(
            "from helpers import PREFIX\n"
            "from worktree_board.plugin import Column, Turn, default\n"
            "COLUMNS = [*default.COLUMNS, Column('parked', '止めている')]\n"
            "def state(wt):\n"
            "    if wt.pr and wt.pr['title'].startswith(PREFIX):\n"
            "        return Turn('parked', 'WIP のまま')\n"
            "    return default.state(wt)\n")
        self.assertEqual([c.id for c in plugin.columns][-1], "parked")
        self.assertEqual(plugin.columns[-1].label, "止めている")
        self.assertEqual(plugin.origin, str(self.root / "p.py"))
        wip = Worktree("h", "o/r", "x", "/w/x", "feat", pr=pr(title="WIP: x"))
        self.assertEqual(plugin.state(wip), Turn("parked", "WIP のまま"))
        self.assertEqual(plugin.state(Worktree("h", "o/r", "x", "/w/x", "feat", pr=pr())).column, "review")

    def test_load_errors_say_which_file_and_line(self):
        self.assertEqual(self.error('[[repos]]\npath = "a"\nplugin = "nope.py"\n')[0], "error.plugin_missing")
        self.write("state.txt", "def state(wt):\n    return None\n")
        key, args = self.error('[[repos]]\npath = "a"\nplugin = "state.txt"\n')
        self.assertEqual((key, args["detail"]), ("error.plugin_load", "not a .py file"))
        self.write("syntax.py", "def state(wt)\n    return None\n")
        key, args = self.error('[[repos]]\npath = "a"\nplugin = "syntax.py"\n')
        self.assertEqual((key, args["path"]), ("error.plugin_load", str(self.root / "syntax.py")))
        self.assertIn("line 1", args["detail"])
        self.write("boom.py", "x = 1\nraise RuntimeError('boom')\n")
        key, args = self.error('[[repos]]\npath = "a"\nplugin = "boom.py"\n')
        self.assertEqual((key, args["detail"]), ("error.plugin_load", "line 2: RuntimeError: boom"))
        self.write("empty.py", "X = 1\n")
        self.assertEqual(self.error('[[repos]]\npath = "a"\nplugin = "empty.py"\n')[0], "error.plugin_no_state")
        for bad in ("[]", "'mine'", "['a', 'a']", "['a', 1]", "['']"):
            self.write("cols.py", f"COLUMNS = {bad}\ndef state(wt):\n    return None\n")
            self.assertEqual(self.error('[[repos]]\npath = "a"\nplugin = "cols.py"\n')[0], "error.plugin_columns", bad)


def plugin_of(state, columns=("mine", "done")):
    return cards.Plugin(state, tuple(Column(c) for c in columns))


class Cards(unittest.TestCase):
    wt = Worktree("h", "o/r", "x", "/w/x", "feat", pr=pr())

    def test_text_and_keys_and_hiding(self):
        p = plugin_of(lambda wt: Turn("done", "おわり", Text("next.cleanup"), ("印",)))
        c = cards.build(p, [self.wt])[0]
        self.assertEqual((c["state"], c["reason"], c["next"], c["badges"]),
                         ("done", {"text": "おわり"}, {"key": "next.cleanup", "args": {}}, [{"text": "印"}]))
        self.assertEqual(cards.build(plugin_of(lambda wt: None), [self.wt]), [])

    def test_a_broken_function_only_breaks_its_card(self):
        def state(wt):
            if wt.name == "bad":
                raise KeyError("labels")
            return Turn("done", "ok")
        bad = Worktree("h", "o/r", "bad", "/w/bad", "b")
        got = {c["label"]: c for c in cards.build(plugin_of(state), [self.wt, bad])}
        self.assertEqual(got["x"]["state"], "done")
        self.assertEqual((got["bad"]["state"], got["bad"]["reason"]),
                         ("mine", {"key": "reason.plugin_failed", "args": {"detail": "KeyError: 'labels'"}}))
        for wrong, detail in ((Turn("nope", "x"), "unknown column 'nope'"), ("mine", "state() returned str, not Turn or None")):
            c = cards.build(plugin_of(lambda wt, w=wrong: w), [self.wt])[0]
            self.assertEqual(c["reason"]["args"]["detail"], detail)

    def test_columns_of_several_repos_are_merged_in_order(self):
        merged = cards.merge_columns([plugin_of(None, ("mine", "review")), plugin_of(None, ("mine", "parked", "review"))])
        self.assertEqual([c.id for c in merged], ["mine", "review", "parked"])

    def test_worktree_round_trips_as_plain_data(self):
        wt = Worktree("h", "o/r", "x", "/w/x", "feat", pr=pr(), sessions=(Session("Claude Code", "idle"),))
        self.assertEqual(Worktree.from_dict(wt.to_dict()), wt)


class FakeSource:
    label = "o/r"

    def __init__(self, plugin):
        self.plugin = plugin

    def fetch(self):
        return {"worktrees": {"feat": "/w/feat"}, "primary": {}, "prs": [pr()], "closed": {}, "me": ME}


class BoardUsesTheRepoPlugin(unittest.TestCase):
    def test_columns_and_states_come_from_the_plugin(self):
        board = Board([FakeSource(plugin_of(lambda wt: Turn("parked", "止めた"), ("mine", "parked")))], sessions=lambda: [], host="h")
        board.tick()
        v = board.view()
        self.assertEqual(v["columns"], [{"id": "mine", "label": None}, {"id": "parked", "label": None}])
        self.assertEqual([(i["state"], i["reason"]) for i in v["worktrees"]], [("parked", {"text": "止めた"})])
        self.assertEqual(collect.host_name(), collect.host_name().split(".")[0])


if __name__ == "__main__":
    unittest.main()
