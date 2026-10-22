"""手元で動いているコーディングエージェントの会話と、その状態。読むだけで、会話には何も送らない。

今読めるのは Claude Code だけ。Claude Code は、開いている会話ごとに <設定のフォルダ>/sessions/<pid>.json を書き、
会話の作業フォルダ（cwd）と状態（status）を入れている。公開された形式ではないので、読めない値の会話は数えない。
会話の記録（projects/<作業フォルダ>/<sessionId>.jsonl）は、プラグインが最後の返事を頼んだときだけ last_reply で読む。
ほかのエージェントを足すときは、同じ形（agent・status・waiting_for・since・cwd・transcript）の一覧を返す関数を sessions() に足す。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

CLAUDE = "Claude Code"
# Claude Code の status → このツールの状態。shell はシェルのコマンドを走らせている間で、Claude Code 自身の会話の一覧も
# busy と同じく作業中と出す。waiting は許可・質問への答えを待っている（何を待つかは waitingFor）、
# idle は返事を終えて次の指示を待っている
CLAUDE_STATUS = {"busy": "working", "shell": "working", "waiting": "waiting", "idle": "idle"}
REPLY_MAX = 4000  # 返事の文は、頭からこの文字数まで渡す
TAIL_FIRST, TAIL_MAX = 256 * 1024, 32 * 1024 * 1024  # 会話の記録は末尾から読み、見つからなければ読む量を増やす
_replies: dict[str, tuple[tuple[int, int], str | None]] = {}  # 記録のパス → ((大きさ, 更新の時刻), 最後の返事)


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
            sid = text(d.get("sessionId"))
            out.append({"agent": CLAUDE, "status": status, "waiting_for": text(d.get("waitingFor")),
                        "since": iso(d.get("statusUpdatedAt") or d.get("updatedAt")), "cwd": cwd,
                        "transcript": str(transcript_path(base or claude_dir(), cwd, sid)) if sid else None})
    return out


def transcript_path(base: Path, cwd: str, session_id: str) -> Path:
    """Claude Code が会話の記録を置く場所。フォルダの名前は、作業フォルダの英数字以外を - にしたもの。"""
    return base / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd) / f"{session_id}.jsonl"


def last_reply(session: dict) -> str | None:
    """会話の中で、エージェントが最後に書いた返事の文。記録が無い・読めなければ None。作業中なら、途中で書いた文のこともある。"""
    path = session.get("transcript")
    try:
        st = os.stat(path) if path else None
    except OSError:
        return None
    if st is None:
        return None
    stamp = (st.st_size, st.st_mtime_ns)
    cached = _replies.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    found = None
    try:
        with open(path, "rb") as fh:
            size = TAIL_FIRST
            while found is None:
                fh.seek(max(0, st.st_size - size))
                lines = fh.read().splitlines()
                if size < st.st_size:
                    lines = lines[1:]  # 途中から読んだ最初の行は欠けている
                found = next((t for t in map(reply_text, reversed(lines)) if t), None)
                if size >= min(st.st_size, TAIL_MAX):
                    break
                size *= 4
    except OSError:
        return None
    _replies[path] = (stamp, found)
    return found


def reply_text(line: bytes) -> str | None:
    """記録の 1 行が、エージェントの返事で文を含むなら、その文。"""
    if b'"assistant"' not in line:  # 大半の行（ツールの結果など）を、JSON を読まずに飛ばす
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        body = content
    elif isinstance(content, list):
        body = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    return body.strip()[:REPLY_MAX] or None


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
