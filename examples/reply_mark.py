"""見本: エージェントの返事の 1 行目に付けた印で、自分の番かを決める。

エージェントに「返事の 1 行目を ✅（終わった）・⏳（裏で続いている）・❓（答えが要る）で始める」と決めている人向け。
エージェントの状態だけでは、文で質問して返事を終えた会話も「指示待ち」にしか見えないので、❓ を読んで自分の番に出す。
同じ worktree に会話がいくつあっても、許可や質問への答えを待つ会話 → ❓ → ⏳ の順に見る。
"""
from worktree_board.plugin import Turn, default

NEEDS = ["last_reply"]  # 会話の記録から、最後の返事を読む


def state(worktree):
    t = default.state(worktree)
    if any(s.status == "waiting" for s in worktree.sessions):
        return t  # 既定の判定が、もう自分の番に出している
    badges = t.badges if t else ()
    idle = [(s.agent, (s.last_reply or "").lstrip().split("\n", 1)[0]) for s in worktree.sessions if s.status == "idle"]
    for agent, first in idle:  # ❓ は、ほかの会話の ⏳ より先に見る
        if first.startswith("❓"):
            return Turn("mine", f"{agent} が質問している: {first[1:].strip()[:60]}", "その会話で答える", badges)
    for agent, first in idle:
        if first.startswith("⏳"):
            return Turn("agent", f"{agent} が裏で作業を続けている", None, badges)
    return t
