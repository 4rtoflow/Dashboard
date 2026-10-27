"""設定ファイル（TOML）を読み、repo ごとの状態を決める関数（プラグイン）を読み込む。

誤りは ConfigError で返す。文章は持たず、翻訳のキー（error.*）と、どのファイルのどこがどうおかしいかの引数で持つ
（AI に設定を書かせたとき、端末の出力だけで直せるようにするため）。
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path

from .cards import DEFAULT, NEEDS, Plugin
from .model import Column

PATH = Path("~/.config/worktree_board/config.toml")
EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.toml"
TOP = {"port": int, "interval": int, "lang": str, "repos": list}
REPO = {"path": str, "name": str, "plugin": str}
TYPE_NAMES = {int: "integer", str: "string", list: "array of tables ([[repos]])"}


class ConfigError(Exception):
    def __init__(self, key: str, /, **args):  # 引数にも key（設定の項目名）が来るので、翻訳のキーは位置でだけ受ける
        super().__init__(key, args)
        self.key, self.params = key, args  # args は Exception の組み込みの名前で、代入するとタプルに直されてしまう

    def to_dict(self) -> dict:
        return {"key": self.key, "args": self.params}


@dataclass(frozen=True)
class Repo:
    path: Path
    name: str | None = None  # OWNER/REPO。書かなければ gh が path の remote から決める
    plugin: Plugin = DEFAULT


@dataclass(frozen=True)
class Config:
    repos: tuple[Repo, ...]
    port: int = 8765
    interval: int = 60
    lang: str | None = None  # 端末に出す言語。書かなければ環境から決める
    path: Path = PATH


def check(where: str, table: dict, known: dict, path: Path) -> None:
    for key, value in table.items():
        if key not in known:
            raise ConfigError("error.config_key", path=str(path), key=f"{where}{key}")
        want = known[key]
        if not isinstance(value, want) or (want is int and isinstance(value, bool)):
            raise ConfigError("error.config_value", path=str(path), key=f"{where}{key}", expected=TYPE_NAMES[want])


def load(path: Path) -> Config:
    import tomllib  # Python 3.11 から。版の確かめは __main__ が先に済ませる

    path = path.expanduser()
    if not path.is_file():
        raise ConfigError("error.config_missing", path=str(path), example=str(EXAMPLE))
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:  # 文に行と列が入っている
        raise ConfigError("error.config_syntax", path=str(path), detail=str(e)) from None
    check("", data, TOP, path)
    repos = data.get("repos") or []
    if not repos:
        raise ConfigError("error.config_no_repos", path=str(path))
    out = []
    for i, r in enumerate(repos, 1):
        where = f"repos[{i}]."
        if not isinstance(r, dict):
            raise ConfigError("error.config_value", path=str(path), key=f"repos[{i}]", expected=TYPE_NAMES[list])
        check(where, r, REPO, path)
        if "path" not in r:
            raise ConfigError("error.config_required", path=str(path), key=f"{where}path")
        plugin = load_plugin(resolve(r["plugin"], path), i) if "plugin" in r else DEFAULT
        out.append(Repo(resolve(r["path"], path), r.get("name"), plugin))
    return Config(tuple(out), data.get("port", 8765), data.get("interval", 60), data.get("lang"), path)


def resolve(value: str, config: Path) -> Path:
    """~ を開き、相対パスは設定ファイルのフォルダから読む。"""
    p = Path(value).expanduser()
    return (p if p.is_absolute() else config.parent / p).resolve()


def load_plugin(file: Path, index: int) -> Plugin:
    """プラグインのファイルを読み込む。同じフォルダのファイルを import できるよう、そのフォルダを読み込み先に足す。"""
    if not file.is_file():
        raise ConfigError("error.plugin_missing", path=str(file))
    folder = str(file.parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    spec = importlib.util.spec_from_file_location(f"worktree_board_plugin_{index}_{file.stem}", file)
    if spec is None or spec.loader is None:  # 名前が .py で終わらないと、Python のファイルとして読み込めない
        raise ConfigError("error.plugin_load", path=str(file), detail="not a .py file")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # 自作のファイルの誤りは、どのファイルの何行目かを出す
        frame = next((f for f in reversed(traceback.extract_tb(e.__traceback__)) if f.filename == str(file)), None)
        line = frame.lineno if frame else getattr(e, "lineno", None)  # 構文の誤りは、frame でなく e.lineno に行がある
        at = f"line {line}: " if line else ""
        raise ConfigError("error.plugin_load", path=str(file), detail=f"{at}{type(e).__name__}: {e}") from None
    state = getattr(module, "state", None)
    if not callable(state):
        raise ConfigError("error.plugin_no_state", path=str(file))
    issue_of = getattr(module, "issue_of", DEFAULT.issue_of)
    if not callable(issue_of):
        raise ConfigError("error.plugin_load", path=str(file), detail="issue_of is not a function")
    fetch = getattr(module, "fetch", None)
    if fetch is not None and not callable(fetch):
        raise ConfigError("error.plugin_load", path=str(file), detail="fetch is not a function")
    return Plugin(state, columns(getattr(module, "COLUMNS", None), file), str(file), needs(getattr(module, "NEEDS", ()), file),
                  issue_of, fetch)


def needs(value, file: Path) -> frozenset[str]:
    """NEEDS は、取ってほしい時間のかかる材料（"issue"・"last_reply"）の並び。書かなければ何も取らない。"""
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ConfigError("error.plugin_needs", path=str(file), value=repr(value))
    unknown = [v for v in value if v not in NEEDS]
    if unknown:
        raise ConfigError("error.plugin_needs", path=str(file), value=repr(unknown[0]))
    return frozenset(value)


def columns(value, file: Path) -> tuple[Column, ...]:
    """COLUMNS は、列の id（文字列）か Column の並び。書かなければ既定の列。"""
    if value is None:
        return DEFAULT.columns
    if not isinstance(value, (list, tuple)) or not all(isinstance(c, (str, Column)) for c in value):
        raise ConfigError("error.plugin_columns", path=str(file))
    out = tuple(c if isinstance(c, Column) else Column(c) for c in value)
    ids = [c.id for c in out]
    if not out or len(set(ids)) != len(ids) or not all(ids):  # 空・重なり・空の id
        raise ConfigError("error.plugin_columns", path=str(file))
    return out
