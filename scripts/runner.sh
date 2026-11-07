#!/usr/bin/env bash
# Runs the bots every 15 minutes (just after each candle closes) until
# RUN_MINUTES have passed, committing state after each tick.
set -u
end=$(( $(date +%s) + ${RUN_MINUTES:-330} * 60 ))
branch="${GITHUB_REF_NAME:-main}"
git config user.name "paper-bot"
git config user.email "paper-bot@users.noreply.github.com"

tick() {
  echo "::group::tick $(date -u +%H:%M:%S)"
  python -m bot.run || echo "::warning::paper bots reported an error"
  case "${LIVE_TRADING:-off}" in
    dry|on) python -m bot.live || echo "::warning::live wallet reported an error" ;;
  esac
  git add docs/data/*.json
  if ! git diff --cached --quiet; then
    git commit -q -m "Bot update" -- docs/data/   # only data, never other staged changes
    for attempt in 1 2 3; do
      git pull -q --rebase origin "$branch" && git push -q origin "HEAD:$branch" && break
      sleep 5
    done
  fi
  echo "::endgroup::"
}

while :; do
  tick
  now=$(date +%s)
  next=$(( (now / 900 + 1) * 900 + 75 ))   # 75s after the next 15-minute close
  [ "$next" -ge "$end" ] && break
  sleep $(( next - now ))
done
