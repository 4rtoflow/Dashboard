#!/usr/bin/env bash
# CI の各ジョブの結果を、Discord の webhook に 1 件の投稿として送る（ci.yml の notify ジョブが呼ぶ）。
#   DISCORD_WEBHOOK_URL  送り先の webhook の URL（secret）。空なら何もしない（fork や、まだ webhook を作っていない repo）
#   DISCORD_THREAD_ID    フォーラムの投稿（スレッド）に送るときの、その ID（variable）。空ならチャンネルに送る
#   NEEDS                ${{ toJSON(needs) }}。ジョブ名 → {result}
#   RUN_URL・EVENT・SHA・REPO  実行のリンク・起動の種類・commit・OWNER/REPO
set -euo pipefail

if [ -z "${DISCORD_WEBHOOK_URL:-}" ]; then
  echo "::notice::DISCORD_WEBHOOK_URL が無いので、Discord に送らない"
  exit 0
fi

payload=$(jq -cn --argjson needs "$NEEDS" --arg url "$RUN_URL" --arg event "$EVENT" --arg sha "${SHA:0:7}" --arg repo "$REPO" '
  ($needs | to_entries) as $jobs
  | [$jobs[] | select(.value.result != "success")] as $bad
  | {failure: "失敗", cancelled: "中止", skipped: "未実行"} as $word
  | {
      allowed_mentions: {parse: []},
      embeds: [{
        # 結果は色の帯と題で見せる。通ったときはジョブを並べず、通らなかったときだけ、そのジョブを書く
        author: {name: $repo, url: ("https://github.com/" + $repo)},
        title: (if $bad == [] then "CI 成功" else "CI 失敗" end),
        url: $url,
        color: (if $bad == [] then 5763719 else 15548997 end),
        description: (if $bad == []
                      then "\($jobs | length) 個のジョブがすべて通りました。"
                      else "\($jobs | length) 個のジョブのうち \($bad | length) 個が通りませんでした。\n\n"
                           + ([$bad[] | "**\(.key)**　\($word[.value.result] // .value.result)"] | join("\n"))
                      end),
        footer: {text: ((if $event == "schedule" then "定期実行" elif $event == "workflow_dispatch" then "手動実行" else $event end)
                        + " · " + $sha)},
        timestamp: (now | todate)
      }]
    }')

url=$DISCORD_WEBHOOK_URL
if [ -n "${DISCORD_THREAD_ID:-}" ]; then
  url="$url?thread_id=$DISCORD_THREAD_ID"
fi
curl -fsS --retry 3 -H "Content-Type: application/json" -d "$payload" "$url" > /dev/null
