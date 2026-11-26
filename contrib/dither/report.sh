#!/usr/bin/env bash
# Keep one issue in the fork for a failing tracking run: open it (or comment on the open one) with the stage and the
# end of the log, and close it when a later run succeeds. Run by .github/workflows/dither.yml.
# usage: report.sh failure <log> | report.sh success
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=dither.conf
source "$here/dither.conf"

title="Upstream tracking failed"
run="${GITHUB_SERVER_URL:-https://github.com}/$FORK/actions/runs/${GITHUB_RUN_ID:-?}"
open=$(gh issue list --repo "$FORK" --state open --search "\"$title\" in:title" --json number,title \
  -q ".[] | select(.title == \"$title\") | .number" | head -n1)

case "${1:-}" in
failure)
  log=${2:?usage: report.sh failure <log>}
  stage=$(cat "${DITHER_STAGE_FILE:-/dev/null}" 2>/dev/null || true)
  body=$(mktemp)
  {
    echo "The ${GITHUB_EVENT_NAME:-?} run $run failed at: **${stage:-before the first stage}**."
    echo
    echo "Nothing was pushed: \`$BRANCH\` and the release tags are where the last good run left them."
    echo "A rebase conflict needs the carried commits rebased by hand onto the new upstream tag and pushed to"
    echo "\`$BRANCH\`; that push runs the same build and test, and releases on success."
    echo
    echo "<details><summary>End of the log</summary>"
    echo
    echo '```'
    tail -n 200 "$log" | sed 's/\x1b\[[0-9;]*m//g'
    echo '```'
    echo
    echo "</details>"
  } > "$body"
  if [ -n "$open" ]; then
    gh issue comment "$open" --repo "$FORK" --body-file "$body"
  else
    gh issue create --repo "$FORK" --title "$title" --body-file "$body"
  fi
  ;;
success)
  if [ -n "$open" ]; then
    gh issue close "$open" --repo "$FORK" --comment "Run $run succeeded."
  fi
  ;;
*)
  echo "usage: report.sh failure <log> | report.sh success" >&2
  exit 2
  ;;
esac
