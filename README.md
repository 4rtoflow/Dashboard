# dashboard-template

自分の git worktree と GitHub の PR、そこで動いているコーディングエージェント（Claude Code）の会話を見て、
「今ボールを持っているのは誰か」をブラウザの Dashboard に出す小さな道具。いくつの repo でもまとめて見られる。
見るだけで、GitHub への書き込み（コメント・ラベル・マージなど）も、エージェントへの送信も一切しない。

```
自分の番   │ エージェント作業中 │ GitHub 待ち │ レビュー待ち │ 完了
```

## 要るもの

- Python 3.11 以上（標準ライブラリだけ。インストールするものは無い。macOS に最初から入っている 3.9 では動かない）
- `git`
- [GitHub CLI](https://cli.github.com/)（`gh`）。`gh auth login` で入っておく
- エージェントの状態も見るなら [Claude Code](https://docs.anthropic.com/en/docs/claude-code)（無くても、PR の判定は動く）

## 使い方

見る repo は設定ファイルに書く。見本の [config.example.toml](config.example.toml) を `~/.config/worktree_board/config.toml` に写して直す。

```bash
cd dashboard-template
python3 -m worktree_board serve    # http://localhost:8765 に Dashboard を出す
python3 -m worktree_board status   # 今の状態を 1 回だけ端末に出す
```

オプションは、設定ファイルの場所を変える `--config PATH` だけ。Dashboard の「今すぐ見直す」で、次の回を待たずに見直せる。止めるのは Ctrl-C。

### 設定ファイル

| 項目 | 意味 |
|---|---|
| `port` | `serve` の待ち受けポート（既定 8765） |
| `interval` | GitHub を見直す間隔の秒数（既定 60） |
| `lang` | 端末に出す言語（省略すると `LC_ALL`・`LC_MESSAGES`・`LANG` から決める。合うものが無ければ英語） |
| `[[repos]]` の `path` | 見る repo の中のフォルダ（primary checkout でも worktree でも）。相対パスは設定ファイルのフォルダから |
| `[[repos]]` の `name` | GitHub の `OWNER/REPO`（省略すると、`gh` が `path` の remote から決める） |
| `[[repos]]` の `plugin` | その repo の状態を決める関数のファイル（省略すると既定の判定。下の「状態を決める関数を差し替える」） |

- 誤り（知らない項目・型の違い・TOML の書き方）は、起動時にファイルと項目・行を出して止まる。
- 同じ repo の primary checkout と worktree を両方書いても、1 つの repo として数える。git の repo でない `path` は、端末に知らせて飛ばす。
- 画面の列は repo をまたいで 1 つにまとめ、カードに repo の名前を付ける（repo が 1 つなら付けない）。
- 取れなかった repo があっても、ほかの repo のカードは出し続ける。取れなかった repo は、前に取れたカードのまま、画面の上にエラーを出す。

### 状態を決める関数を差し替える

`plugin` に書いた Python のファイルの `state(worktree)` が、その repo の worktree ごとに呼ばれる。使えるものと決まりは
[worktree_board/plugin.py](worktree_board/plugin.py) の説明にある。設定ファイルと同じフォルダに置くとよい。

```python
from worktree_board.plugin import Turn, default

def state(worktree):
    if worktree.pr and worktree.pr["title"].startswith("WIP"):
        return Turn("mine", "WIP のまま", "仕上げて WIP を外す")
    return default.state(worktree)   # 残りは既定の判定に任せる
```

- `None` を返すとカードを出さない。列は `COLUMNS` で決められる（書かなければ既定の列）。
- 関数が例外を投げたり、知らない列を返したりすると、そのカードだけを先頭の列に「関数が失敗した」と出す。
- 直したら、起動し直すと反映される。

## 何を見ているか

- worktree: `git worktree list` のうち、ブランチを持つもの。primary checkout は、開いた PR があるか、エージェントが作業しているか答えを待っているときだけ出す（既定の判定）
- PR: その repo の、自分が作った PR（開いているもの・閉じたもの）。worktree の無い開いた PR も 1 枚になる
- エージェントの会話: 手元で開いている Claude Code の会話（下の「エージェントの会話の読み方」）

worktree のブランチと PR のブランチを突き合わせて、1 つの worktree を 1 枚のカードにする。
エージェントの会話は、作業フォルダを含む worktree のカードに付く。

### エージェントの会話の読み方

Claude Code は、開いている会話ごとに `~/.claude/sessions/<プロセス番号>.json` を書き、会話の作業フォルダと状態を入れている。
これを読むだけなので、設定は要らない（`CLAUDE_CONFIG_DIR` で設定のフォルダを変えていれば、そちらを読む）。

| Claude Code の状態 | このツールでの扱い |
|---|---|
| 許可や質問への答えを待っている | 自分の番（何を待っているかも出す） |
| 作業している（シェルのコマンドを走らせている間も） | エージェント作業中 |
| 返事を終えて、次の指示を待っている | 列は PR の様子のまま。カードに「指示待ち」の印を付ける |

- プロセスが終わった会話のファイルが残っていても数えない。
- primary checkout（`main` で直接作業しているときなど）は、開いた PR があるか、エージェントが作業しているか答えを待っているときだけカードになる（既定の判定）。
- 会話の様子は、PR の見直しを待たずに、画面が取り直すたび（10 秒おき）に新しくなる。
- この形式は Claude Code が公開しているものではない。Claude Code の版によって読めなくなったら、エージェントの会話が出ないだけで、PR の判定はそのまま動く。
- ほかのエージェントを足すときは、`worktree_board/agents.py` に、同じ形の一覧を返す読み方を足して `sessions()` に加える。

## 状態の決め方

上から順に当てはめ、先に当たったものが勝つ（`worktree_board/judge.py`）。

| 順 | 条件 | 状態 | 次にすること |
|---|---|---|---|
| 1 | エージェントの会話が答えを待っている（同じ worktree でほかの会話が動いていても） | 自分の番 | その会話で答える |
| 2 | エージェントの会話が作業している | エージェント作業中 | 待つ |
| 3 | PR が閉じている（開いた PR が無い） | 完了 | worktree を片付ける |
| 4 | PR がまだ無い | 自分の番 | 作業を進めるか、PR を作る |
| 5 | ベースブランチとコンフリクトしている | 自分の番 | 取り込んで解く |
| 6 | CI が失敗している（同じチェックは最新の run だけ見る） | 自分の番 | 直して push する |
| 7 | 今の commit に対して変更が求められている | 自分の番 | 指摘を直して push する |
| 8 | CI が走っている | GitHub 待ち | 待つ |
| 9 | Draft のまま | 自分の番 | Ready にする |
| 10 | 変更を求められた後に push した（再レビュー待ち） | レビュー待ち | 待つ |
| 11 | 承認されている | 自分の番 | マージする |
| 12 | それ以外 | レビュー待ち | 待つ |

同じ列の中では、PR が長く動いていないものを上に出す。

## 言語とテーマ

画面の右上で、言語と画面の色を選べる。選んだものはそのブラウザに覚えさせる（サーバーには何も送らない）。

- **言語（21）**: English・日本語・简体中文・繁體中文・한국어・Español・Français・Deutsch・Italiano・Português (Brasil)・Русский・العربية・हिन्दी・Bahasa Indonesia・Türkçe・Tiếng Việt・ไทย・Nederlands・Polski・Українська・toki pona
  - 最初は、ブラウザの言語に合うものを選ぶ（合うものが無ければ英語）。
  - アラビア語は右から左の表示になる。
  - 端末の `status` と `--help`、取得のエラーも同じ言語で出る。argparse が自分で出す定型文（`usage:`・`options:` など）だけは英語のまま。
- **画面の色**: 自動（OS の設定に合わせる）・明るい・暗い。

### 言語を足す・直す

文章は `worktree_board/locales/<コード>.json` にあり、画面と端末が同じファイルを読む。
`en.json` を写して `_name`（その言語での名前）と `_dir`（`ltr` か `rtl`）を決め、値を訳す。
`{n}` のような `{名前}` はそのまま残す。コードは ISO 639（`ja`・`tok` など）で、中国語だけ `zh-Hans`・`zh-Hant`。
足りないキーは英語で補われる。テスト（`tests/test_i18n.py`）が、全言語のキーと `{名前}` が英語と揃っているかを見る。

判定（`judge.py`）は文章を持たず、`reason.ci_failed` のようなキーと引数だけを返す。だから言語を切り替えても、判定の中身は変わらない。

## 安全について

- `127.0.0.1` だけで待ち受ける。`localhost` 以外の名前で来た要求は断る。
- 書き込みの API（見直しの依頼）は、独自ヘッダーが無ければ断る（ほかのサイトのページから勝手に押されないため）。
- 記録は何も保存しない（ファイルを作らない）。
- エージェントの会話は、`~/.claude/sessions/` の JSON を読むだけ。会話の中身（やりとりの記録）は読まず、会話へは何も送らない。

## テスト

```bash
python3 -m unittest discover -s tests -t .
```

## 構成

| ファイル | 役割 |
|---|---|
| `worktree_board/judge.py` | 既定の状態を決める関数（副作用なし。文章ではなく翻訳のキーを返す） |
| `worktree_board/plugin.py` | 状態を決める関数を自作する人向けの窓口 |
| `worktree_board/model.py` | 関数が受け取る worktree と、返す答えの形（ただのデータ） |
| `worktree_board/config.py` | 設定ファイルを読み、関数のファイルを読み込む |
| `worktree_board/collect.py` | worktree を集める（今は手元の PC だけ） |
| `worktree_board/cards.py` | worktree ごとに関数を呼び、カードにする |
| `worktree_board/i18n.py` | 翻訳の読み込みと、言語の決め方 |
| `worktree_board/locales/*.json` | 言語ごとの文章（21 言語） |
| `worktree_board/sources.py` | `git` と `gh` から材料を取る |
| `worktree_board/agents.py` | エージェントの会話を読み、worktree に振り分ける |
| `worktree_board/server.py` | 定期的な見直しと、Dashboard・API を出す HTTP サーバー |
| `worktree_board/dashboard.html` | 画面（言語・画面の色の切り替えを含む） |
| `worktree_board/__main__.py` | コマンド（`serve`・`status`） |
| `tests/test_judge.py` | 判定の順・境界・複数の repo・サーバーの防御のテスト |
| `tests/test_i18n.py` | 全言語のキーと `{名前}` の一致・言語の決め方のテスト |
| `tests/test_agents.py` | 会話の読み方・振り分け・判定の順・primary checkout のカードのテスト |
| `tests/test_config.py` | 設定ファイルの誤りの知らせ方・関数の読み込み・関数が失敗したときのテスト |

## API

| | |
|---|---|
| `GET /` | Dashboard |
| `GET /api/worktrees` | 列の並び・カードの一覧・見ている repo ごとのエラー（JSON）。カードには、そこで開いているエージェントの会話の状態も付く。理由・次にすること・印は、翻訳のキーと引数（`{key, args}`）か、関数が書いた文（`{text}`） |
| `GET /api/locales` | 言語の一覧（コード・その言語での名前・文字の向き） |
| `GET /locales/<コード>.json` | その言語の文章 |
| `POST /api/refresh` | 次の回を待たずに見直す（`X-Dashboard: 1` ヘッダーが要る） |
