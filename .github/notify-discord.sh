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
  | ([$jobs[] | select(.value.result != "success")] | length) as $bad
  | {
      allowed_mentions: {parse: []},
      embeds: [{
        title: ((if $bad == 0 then "✅ CI が通った" else "❌ CI が落ちた" end) + ": " + $repo),
        url: $url,
        color: (if $bad == 0 then 3066993 else 15158332 end),
        description: ([$jobs[] | (if .value.result == "success" then "✅" elif .value.result == "skipped" then "⏭️" else "❌" end)
                       + " `" + .key + "` " + .value.result] | join("\n")),
        footer: {text: ((if $event == "schedule" then "定期実行" elif $event == "workflow_dispatch" then "手動実行" else $event end)
                        + " · " + $sha)}
      }]
    }')

url=$DISCORD_WEBHOOK_URL
if [ -n "${DISCORD_THREAD_ID:-}" ]; then
  url="$url?thread_id=$DISCORD_THREAD_ID"
fi
curl -fsS --retry 3 -H "Content-Type: application/json" -d "$payload" "$url" > /dev/null
