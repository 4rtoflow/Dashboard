"""翻訳。言語ごとの JSON（locales/<コード>.json）を、画面（dashboard.html）と CLI の両方が使う。

英語（en）が基準で、ほかの言語に無いキーは英語で補う。文中の {名前} は引数で置き換え、引数が配列なら list.sep でつなぐ。
"""
from __future__ import annotations

import json
import os
import re
from functools import cache
from pathlib import Path

LOCALES = Path(__file__).with_name("locales")
DEFAULT = "en"


def available() -> list[str]:
    return sorted(p.stem for p in LOCALES.glob("*.json"))


@cache
def _read(code: str) -> dict:
    return json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))


def catalog(code: str) -> dict:
    """code の翻訳。足りないキーは英語で補う。"""
    return {**_read(DEFAULT), **(_read(code) if code in available() else {})}


def languages() -> list[dict]:
    """言語の選択肢（コード・その言語での名前・文字の向き）。"""
    return [{"code": c, "name": _read(c)["_name"], "dir": _read(c)["_dir"]} for c in available()]


def normalize(tag: str | None) -> str | None:
    """ja_JP.UTF-8・zh-TW・pt-PT のような言語の名前を、ある言語のコードに直す。合うものが無ければ None。"""
    if not tag:
        return None
    parts = re.split(r"[-_]", tag.split(".")[0].split("@")[0].lower())
    codes = {c.lower(): c for c in available()}
    if parts[0] == "zh":
        traditional = "hant" in parts or any(p in ("tw", "hk", "mo") for p in parts[1:])
        return "zh-Hant" if traditional else "zh-Hans"
    return codes.get(parts[0])


def detect(env=None) -> str:
    """環境変数（LC_ALL・LC_MESSAGES・LANG）から言語を決める。決められなければ英語。"""
    env = os.environ if env is None else env
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if code := normalize(env.get(name)):
            return code
    return DEFAULT


def fmt(msgs: dict, key: str, args: dict | None = None) -> str:
    sep = msgs.get("list.sep", ", ")

    def value(m: re.Match) -> str:
        v = (args or {}).get(m.group(1))
        return m.group(0) if v is None else sep.join(map(str, v)) if isinstance(v, (list, tuple)) else str(v)

    return re.sub(r"\{(\w+)\}", value, msgs.get(key, key))


def say(msgs: dict, message: dict | None) -> str:
    """カードの理由・次にすること・印の 1 つ（{key, args} か {text}）を、その言語の文にする。"""
    if not message:
        return ""
    return message["text"] if "text" in message else fmt(msgs, message["key"], message.get("args"))


def describe_error(msgs: dict, error: dict) -> str:
    """{key, args} の取得エラーを、その言語の 1 文にする。"""
    return fmt(msgs, "error.fetch", {"detail": fmt(msgs, error["key"], error.get("args"))})
