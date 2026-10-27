# AGENTS.md

このリポジトリは、git worktree・GitHub の PR・コーディングエージェントの会話から「今ボールを持っているのは誰か」をブラウザに出す Dashboard のテンプレート。
この文書は、使う人に頼まれて作業する AI 向け。頼まれごとに合わせて、次のどれかに従う。

- 「設定して」「自分用にして」「動かして」 → [1. 設定する](#1-設定する)
- 「判定を変えて」「列を足して」「この状態のときは〇〇に出して」 → [2. プラグインを書く](#2-プラグインを書く)
- テンプレート自体を直す → [3. テンプレートを直す](#3-テンプレートを直す)

## 守ること

- GitHub とエージェントへは書き込まない。`gh` は読むだけに使う（PR・Issue・ラベル・コメントを作ったり変えたりしない）。プラグインの中でも同じ。
- 設定ファイルとプラグインは、使う人の設定フォルダ `~/.config/worktree_board/` に置く。このリポジトリや、見ている repo の中には置かない（repo やパスの名前を、このリポジトリに commit しない）。
- 既にある設定ファイルを黙って上書きしない。読んでから足すか、直す前に人に確かめる。
- 決めにくいこと（どの repo を見るか、チームの進め方）は推測せずに人に聞く。

## 1. 設定する

コマンド（`python3 -m worktree_board ...`）は、このリポジトリのフォルダ（この文書がある所）で打つ。ほかのフォルダからは `No module named worktree_board` になる。

1. 要るものを確かめる。足りなければ、入れ方を示して人に任せる（勝手に入れない）。
   - `python3 --version` が 3.11 以上（macOS に最初から入っている 3.9 では動かない。Homebrew などで入れる）
   - `git --version`
   - `gh auth status` でログイン済み
   - エージェントの状態も見るなら Claude Code（無くても PR の判定は動く）
2. 人に聞く。
   - 見る repo のフォルダ（いくつでもよい。repo の中のフォルダならどこでもよく、worktree を全部書く必要は無い）
   - ポートを変えるか（既定 8765）。使う番号が空いているかを `lsof -nP -iTCP:<番号> -sTCP:LISTEN` で確かめる（何か出たら使われている。変えた先の番号も確かめる）
3. [config.example.toml](config.example.toml) を `~/.config/worktree_board/config.toml` に写して書く（項目は下の表）。
4. `python3 -m worktree_board status` を打つ。誤りが出たら、そのメッセージ（ファイル・項目・行）に従って直し、打ち直す。
5. 出力を人に見せ、[既定の判定](#既定の判定)の表と合わせて、次を聞く。合わないところがあれば [2. プラグインを書く](#2-プラグインを書く)。
   - 承認された PR は誰がマージするか（既定は「承認されたら自分の番＝自分でマージ」）
   - Issue のラベルや状態で、止めている・待っているものを表しているか
   - Draft・CI・レビューの扱いで、既定と違う決まりがあるか
   - エージェントに、返事の決まった書き方（1 行目の印など）をさせているか
6. `python3 -m worktree_board serve` で動かし、`http://localhost:<port>` を開いてもらう。止めるのは Ctrl-C。常に動かしておきたいと言われたら、その OS のやり方（launchd・systemd など）を相談する（このテンプレートには入っていない）。

### 設定ファイルの項目

| 項目 | 意味 |
|---|---|
| `port` | `serve` の待ち受けポート（既定 8765） |
| `interval` | GitHub を見直す間隔の秒数（既定 60） |
| `lang` | 端末に出す言語のコード（`ja`・`en` など。既定は `LC_ALL`・`LC_MESSAGES`・`LANG` から決め、合うものが無ければ英語）。画面の言語は画面の右上で選ぶ |
| `[[repos]]` の `path`（必須） | 見る repo の中のフォルダ。`~` と、設定ファイルのフォルダからの相対パスが使える |
| `[[repos]]` の `name` | GitHub の `OWNER/REPO`（既定は `gh` が `path` の remote から決める） |
| `[[repos]]` の `plugin` | その repo の状態を決める関数のファイル（既定は下の既定の判定） |

- 設定ファイルの場所は `--config PATH` で変えられる（同じ PC で 2 つ目の Dashboard を動かすときなど）。オプションはこれだけ。
- 同じ repo の primary checkout と worktree を両方書いても、1 つの repo として数える（先に書いたほうの `name`・`plugin` を使う）。git の repo でない `path` は、端末に知らせて飛ばす。

### 既定の判定

worktree 1 つを 1 枚のカードにし、上から順に当てはめ、先に当たったものが勝つ（`worktree_board/judge.py`）。

| 順 | 条件 | 列 | 次にすること |
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

- 見る PR は、自分が作ったもの（`gh pr list --author @me`）。worktree の無い開いた PR も 1 枚になる。
- primary checkout は、そのブランチに開いた PR があるか、エージェントが作業しているか答えを待っているときだけ出す（`main` で直接作業していても見落とさない）。
- 同じ列の中では、PR が長く動いていないものを上に出す。

### エージェントの会話の読み方

Claude Code が、開いている会話ごとに書く `~/.claude/sessions/<プロセス番号>.json` を読む（`CLAUDE_CONFIG_DIR` があればその下）。設定は要らない。

| Claude Code の状態 | 扱い |
|---|---|
| 許可や質問への答えを待っている | 自分の番（何を待っているかも出す） |
| 作業している（シェルのコマンドを走らせている間も） | エージェント作業中 |
| 返事を終えて、次の指示を待っている | 列は PR の様子のまま。カードに「指示待ち」の印を付ける |

- 会話の作業フォルダを含む、いちばん深い worktree のカードに付く。プロセスが終わった会話は数えない。
- 会話の様子は、PR の見直しを待たずに、画面が取り直すたび（10 秒おき）に新しくなる。
- この形式は Claude Code が公開しているものではない。読めなくなっても、会話が出ないだけで PR の判定は動く。

## 2. プラグインを書く

プラグインは、repo ごとに「状態を決める関数」を差し替える Python のファイル。既定の判定を呼び、違うところだけを上書きするのが基本。

1. [worktree_board/plugin.py](worktree_board/plugin.py) の説明を読む（使えるもの・決まりはここにある）。中身の形は [worktree_board/model.py](worktree_board/model.py)。
2. 近い見本を [examples/](examples/) から選ぶ。
   - [reply_mark.py](examples/reply_mark.py): エージェントの返事の 1 行目の印（❓・⏳）で、自分の番かを決める（会話が複数あっても、答え待ち → ❓ → ⏳ の順に見る）
   - [issue_labels.py](examples/issue_labels.py): Issue のラベルで、止めている worktree を専用の列に分ける
3. `~/.config/worktree_board/` に書き、使う `[[repos]]` に `plugin = "<そのファイル名>"` を書く。ファイル名は自由（例: `my_repo.py`）。
   同じファイルを複数の `[[repos]]` から指してよい（repo ごとに別々に読み込まれる）。全部の repo にまとめてかける書き方は無いので、`[[repos]]` ごとに書く。
4. `python3 -m worktree_board status` で確かめ、人に見せる。関数が失敗したカードは、先頭の列に「状態を決める関数が失敗した: <例外>」と出るので、それに従って直す。
5. `serve` を動かしていたら、起動し直すと反映される。

```python
from worktree_board.plugin import Turn, default

def state(worktree):
    if worktree.pr and worktree.pr["title"].startswith("WIP"):
        return Turn("mine", "WIP のまま", "仕上げて WIP を外す")
    return default.state(worktree)  # 残りは既定の判定に任せる
```

### 関数が受け取るもの（`Worktree`）

| 欄 | 中身 |
|---|---|
| `host`・`repo`・`name` | PC の名前・`OWNER/REPO`・画面に出す名前（フォルダ名。worktree の無い PR は `pr-<番号>`） |
| `path`・`branch`・`primary` | worktree のパス（worktree の無い PR なら None）・ブランチ・primary checkout か |
| `pr` | 開いている自分の PR（`gh pr list --json` の 1 件。欄は `sources.FIELDS`）。無ければ None |
| `closed` | 開いた PR が無いときの、同じブランチの閉じた PR（欄は `sources.CLOSED_FIELDS`） |
| `sessions` | 開いているエージェントの会話（`Session`: `agent`・`status`（working・waiting・idle）・`waiting_for`・`since`・`last_reply`） |
| `me` | GitHub のログイン名 |
| `issue_number` | ブランチ名から引いた Issue の番号（区切りに挟まれた数。`feature/5323-x` なら 5323）。決まりが違えば `issue_of(branch)` を書く |
| `issue` | Issue（`number`・`title`・`state`・`stateReason`・`url`・`updatedAt`・`labels`（名前の並び）・`assignees`（ログイン名の並び）） |

- `issue` と `Session.last_reply` は、時間がかかるので、プラグインの `NEEDS` に `"issue"`・`"last_reply"` を書いたときだけ入る。要るものだけを書く。
- `state` は画面が読み直すたび（10 秒おき）に呼ばれる。ネットワーク・重い処理・書き込みをしない。要る材料は `NEEDS` で頼む。

### 関数が返すもの

- `Turn(column, reason, next=None, badges=())` か `None`（カードを出さない）。
- `column` は `COLUMNS` の id。`COLUMNS` を書かなければ既定の列（`mine`・`agent`・`github`・`review`・`done`）。列を足すなら `Column("id", "画面に出す名前")` を並べる。
- `reason`・`next`・`badges` は文をそのまま書いてよい（使う人の言語で書く）。`next` は「自分の番」のときだけ書くと、既定の見た目に揃う。

### プラグインを確かめる

`status` の出力のほかに、`Worktree` を作って `state` を呼べば、GitHub を見ずに確かめられる（[tests/test_examples.py](tests/test_examples.py) が例）。

```python
from worktree_board.plugin import Session, Worktree
wt = Worktree("h", "o/r", "x", "/w/x", "feature/12-x", sessions=(Session("Claude Code", "idle", last_reply="❓ 確認: どちらにしますか"),))
print(state(wt))
```

## 3. テンプレートを直す

- テスト: リポジトリの直下で `python3 -m unittest discover -s tests -t .`。直したら全部通してから終える。判定や画面を変えたら、下の「CI」の `e2e/` も流す。
- Python 3.11 以上の標準ライブラリだけを使う。画面（`dashboard.html`）は外から何も読み込まない。CI の中だけで使う道具は別（下の「CI」）。
- 文言は `worktree_board/locales/<コード>.json`（21 言語）。既定の判定と画面は文章を持たず、翻訳のキーと引数を返す。キーを足したら 21 言語すべてに同じキーを足す（`tests/test_i18n.py` が見る）。言語を足すときは `en.json` を写して `_name`・`_dir` を決め、`{名前}` は残して訳す。
- `worktree_board/plugin.py` に出しているものは、使う人のプラグインが頼っている。名前や形を変えるなら、古い形も動くようにする。`examples/` を直したら `tests/test_examples.py` も合わせる。
- 動きを変えたら、この文書と README も同じ変更の中で直す。README は短く保ち、詳しい説明はこの文書に書く。
- 関数に渡す `Worktree` はただのデータに保つ（関数の中から手元の PC に問い合わせない）。別の PC から届いた worktree も同じ関数で判定できるようにするため。

### CI

`.github/workflows/ci.yml` が、毎日 1 回（日本時間 9:23）と手で起動したとき（`gh workflow run ci.yml`）に次を流す。push や PR では走らない。ruff・mypy・coverage・Playwright は CI の中だけで使う道具で、版は `.github/ci-requirements.txt` に固定している（Dashboard を動かすのには要らない）。

| ジョブ | 確かめること |
|---|---|
| `test` | `tests/` を Ubuntu・macOS と Python 3.11〜3.14 で流す |
| `old-python` | macOS に付いている Python 3.9 で打っても、版が足りないと出して止まる |
| `lint` | ruff（誤りを拾う規則と import の並び）・mypy（`worktree_board/`・`examples/` の型）・actionlint（CI の設定） |
| `coverage` | `tests/` のカバレッジが、`pyproject.toml` の `fail_under` を下回らない |
| `e2e` | `e2e/` で画面を Chromium で開き、カードの列・21 言語の文・画面の色・スマホの幅・見直しのボタン・外のサイトへ要求しないこと・ほかのサイトのページから API を使えないことを見る |

手元で同じものを流すときは、リポジトリの外に venv を作って道具を入れる。

```bash
python3 -m venv /tmp/wb-ci && /tmp/wb-ci/bin/pip install -c .github/ci-requirements.txt ruff mypy coverage playwright
/tmp/wb-ci/bin/ruff check . && /tmp/wb-ci/bin/mypy
/tmp/wb-ci/bin/coverage run -m unittest discover -s tests -t . && /tmp/wb-ci/bin/coverage report
/tmp/wb-ci/bin/playwright install chromium && /tmp/wb-ci/bin/python -m unittest discover -s e2e -t .
```

- Playwright が無いと、`e2e/` は飛ばされる（CI では飛ばさずに落ちる）。
- カバレッジが上がったら `fail_under` を上げてよい。下げるのは、コードを消して分母が変わったときだけにする。

### 構成

| ファイル | 役割 |
|---|---|
| `worktree_board/__main__.py` | コマンド（`serve`・`status`） |
| `worktree_board/config.py` | 設定ファイルを読み、プラグインを読み込む |
| `worktree_board/plugin.py` | プラグインを書く人向けの窓口 |
| `worktree_board/model.py` | 関数が受け取る worktree と、返す答えの形（ただのデータ） |
| `worktree_board/judge.py` | 既定の判定（副作用なし。文章ではなく翻訳のキーを返す） |
| `worktree_board/sources.py` | `git` と `gh` から材料を取る（Issue は GraphQL でまとめて取る） |
| `worktree_board/agents.py` | エージェントの会話を読み、worktree に振り分ける。ほかのエージェントは `sessions()` に足す |
| `worktree_board/collect.py` | worktree を集める 1 か所（今は手元の PC だけ） |
| `worktree_board/cards.py` | worktree ごとに関数を呼び、カードにする。関数の失敗はそのカードだけに閉じ込める |
| `worktree_board/server.py` | 定期的な見直しと、Dashboard・API を出す HTTP サーバー |
| `worktree_board/dashboard.html` | 画面（言語・画面の色の切り替えを含む） |
| `worktree_board/i18n.py`・`locales/*.json` | 翻訳の読み込みと言語の決め方・言語ごとの文章 |
| `examples/` | プラグインの見本 |

### API

| | |
|---|---|
| `GET /` | Dashboard |
| `GET /api/worktrees` | 列の並び・カードの一覧・見ている repo ごとのエラー（JSON）。理由・次にすること・印は、翻訳のキーと引数（`{key, args}`）か、関数が書いた文（`{text}`） |
| `GET /api/locales` | 言語の一覧（コード・その言語での名前・文字の向き） |
| `GET /locales/<コード>.json` | その言語の文章 |
| `POST /api/refresh` | 次の回を待たずに見直す（`X-Dashboard: 1` ヘッダーが要る） |

- `127.0.0.1` だけで待ち受け、`Host` が `localhost`・`127.0.0.1` 以外の要求と、ヘッダーの無い書き込みの要求は断る。記録は何も保存しない。
