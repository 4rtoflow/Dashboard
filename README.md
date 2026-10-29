# Dashboard

自分の git worktree・GitHub の PR・コーディングエージェント（Claude Code）の会話を見て、
「今ボールを持っているのは誰か」をブラウザに出す Dashboard。いくつの repo でもまとめて見られる。
見るだけで、GitHub にもエージェントにも何も書き込まない。

```
自分の番 │ エージェント作業中 │ GitHub 待ち │ レビュー待ち │ 完了
```

## 始め方

1. このリポジトリを clone する。
2. そのフォルダで、AI（Claude Code・Codex など）にこう頼む。

   > AGENTS.md を読んで、この Dashboard を自分用に設定して

   AI が、見る repo やチームの進め方を聞きながら、設定ファイル（`~/.config/worktree_board/config.toml`）を書く。
   判定を自分の進め方に合わせたいときも、そのまま AI に頼めばよい（例:「Issue に blocked が付いたものは別の列にして」）。
3. このリポジトリのフォルダで動かす。

   ```bash
   cd dashboard-template
   python3 -m worktree_board serve   # http://localhost:8765
   ```

要るもの: Python 3.11 以上・`git`・ログイン済みの [GitHub CLI](https://cli.github.com/)（`gh`）。
エージェントの状態も見るなら [Claude Code](https://docs.anthropic.com/en/docs/claude-code)。
自分で設定するなら、[config.example.toml](config.example.toml) を写して直す。詳しい仕組みは [AGENTS.md](AGENTS.md)。

画面の右上で、21 の言語と画面の色（自動・明るい・暗い）を選べる。

## 安全について

- `127.0.0.1` だけで待ち受け、ほかのサイトのページからの要求は断る。記録は何も保存しない。
- `gh` は読むだけに使う。エージェントの会話は、Claude Code が書く状態のファイルを読むだけ。
  会話の中身は、判定を差し替えるプラグインが頼んだときだけ、最後の返事を読む。
- 判定を差し替えるプラグインは、Python のコードとしてそのまま動く。設定ファイルには、自分で書いたか中身を確かめたファイルだけを書く。
