"""手元で動いているコーディングエージェントの会話と、その状態。読むだけで、会話には何も送らない。

今読めるのは Claude Code だけ。Claude Code は、開いている会話ごとに <設定のフォルダ>/sessions/<pid>.json を書き、
会話の作業フォルダ（cwd）と状態（status）を入れている。公開された形式ではないので、読めない値の会話は数えない。
ほかのエージェントを足すときは、同じ形（agent・status・waiting_for・since・cwd）の一覧を返す関数を sessions() に足す。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

CLAUDE = "Claude Code"
# Claude Code の status → このツールの状態。waiting は許可・質問への答えを待っている（何を待つかは waitingFor）、
# idle は返事を終えて次の指示を待っている
CLAUDE_STATUS = {"busy": "working", "waiting": "waiting", "idle": "idle"}


def claude_dir(env=None) -> Path:
    """Claude Code の設定のフォルダ。CLAUDE_CONFIG_DIR で変えられる。"""
    env = os.environ if env is None else env
    return Path(env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def alive(pid) -> bool:
    """そのプロセスが生きているか。落ちた会話のファイルが残っていても数えない。"""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name != "posix":  # Windows の os.kill(pid, 0) は、確かめるのではなくプロセスを止めてしまう
        return True
    try:
        os.kill(pid, 0)
    except PermissionError:  # ほかのユーザーのプロセスでも、生きてはいる
        return True
    except OSError:
        return False
    return True


def iso(ms) -> str | None:
    if not isinstance(ms, (int, float)) or isinstance(ms, bool) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="seconds")


def text(v) -> str | None:
    return v if isinstance(v, str) and v else None


def claude_sessions(base: Path | None = None) -> list[dict]:
    """Claude Code の会話のうち、プロセスが生きていて状態が分かるもの。"""
    out = []
    for f in sorted((base or claude_dir()).joinpath("sessions").glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):  # 書き換えている途中に読んだときなど。次に見るときに読める
            continue
        if not isinstance(d, dict):
            continue
        status, cwd = CLAUDE_STATUS.get(d.get("status")), text(d.get("cwd"))
        if status and cwd and alive(d.get("pid")):
            out.append({"agent": CLAUDE, "status": status, "waiting_for": text(d.get("waitingFor")),
                        "since": iso(d.get("statusUpdatedAt") or d.get("updatedAt")), "cwd": cwd})
    return out


def sessions() -> list[dict]:
    """読めるエージェントの会話のすべて。"""
    return claude_sessions()


def assign(found: list[dict], paths: list[str]) -> dict[str, list[dict]]:
    """会話を、その作業フォルダを含むいちばん深いパス（worktree・primary checkout）に振り分ける。
    repo の中に別の worktree を置いていても、深いほうに入る。どのパスにも入らない会話は捨てる。"""
    real = sorted(((p, os.path.realpath(p)) for p in set(paths)), key=lambda pr: -len(pr[1]))
    out: dict[str, list[dict]] = {}
    for s in found:
        cwd = os.path.realpath(s["cwd"])
        for path, r in real:
            if cwd == r or cwd.startswith(r.rstrip(os.sep) + os.sep):
                out.setdefault(path, []).append(s)
                break
    return out
