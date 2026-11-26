#!/usr/bin/env bash
# Keep this fork's branch on upstream's newest stable tag, build it, smoke-test it and release it.
# Run by .github/workflows/dither.yml; runnable by hand with DRY_RUN=1 (nothing is pushed or released).
#
# usage: ci.sh track   (scheduled or dispatched) rebase the carried commits onto upstream's newest stable tag if it
#                      is newer than the one the branch is on, then build, test and release if HEAD has no release
#        ci.sh check   (a push to the branch) build, test and release what was pushed, if it has no release yet
#
# A release is a tag <upstream tag>-dither.<n> on the tested commit, pushed with the branch in one atomic push, and a
# GitHub release holding <NAME>.nix (consumer.sh). Nothing is pushed unless the build and the smoke test pass, so on
# any failure the branch and every tag stay where the last good run left them.
#
# Writes the stage it is in to $DITHER_STAGE_FILE (if set), for report.sh.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)
# shellcheck source=dither.conf
source "$here/dither.conf"
cd "$root"

mode=${1:?usage: ci.sh track|check}
case "$mode" in track | check) ;; *) echo "usage: ci.sh track|check" >&2; exit 2 ;; esac
dry=${DRY_RUN:-}
nixcmd=(nix --extra-experimental-features nix-command)

stage() {
  echo "::group::$1" 2>/dev/null || true
  echo "== $1"
  [ -n "${DITHER_STAGE_FILE:-}" ] && echo "$1" > "$DITHER_STAGE_FILE"
  return 0
}
endgroup() { echo "::endgroup::"; }

work=$(mktemp -d "${RUNNER_TEMP:-/tmp}/dither-ci-XXXXXX")
trap 'rm -rf "$work"' EXIT

stage "Find upstream's newest stable tag"
# DITHER_UPSTREAM_URL: another upstream to fetch tags from (a local repository, to try a rebase by hand).
git fetch --quiet --no-tags "${DITHER_UPSTREAM_URL:-https://github.com/$UPSTREAM.git}" '+refs/tags/*:refs/upstream-tags/*'
stable=$(git for-each-ref --format='%(refname:lstrip=2)' refs/upstream-tags | grep -E "$STABLE_TAG_RE" | sort -V)
newest=$(tail -n1 <<<"$stable")
base=
for t in $(sort -rV <<<"$stable"); do
  if git merge-base --is-ancestor "refs/upstream-tags/$t" HEAD; then base=$t; break; fi
done
[ -n "$newest" ] || { echo "no stable tag upstream matches $STABLE_TAG_RE"; exit 1; }
[ -n "$base" ] || { echo "HEAD is on none of upstream's stable tags"; exit 1; }
echo "upstream's newest stable tag: $newest; $BRANCH is on $base ($(git rev-list --count "refs/upstream-tags/$base..HEAD") commits on top)"
start=$(git rev-parse HEAD)
endgroup

if [ "$mode" = track ] && [ "$newest" != "$base" ] && [ "$(printf '%s\n%s\n' "$base" "$newest" | sort -V | tail -n1)" = "$newest" ]; then
  stage "Rebase onto $newest"
  if ! git rebase --onto "refs/upstream-tags/$newest" "refs/upstream-tags/$base"; then
    echo
    echo "Rebasing $BRANCH from $base onto $newest stopped at:"
    git log -1 --format='  %h %s' REBASE_HEAD 2>/dev/null || true
    echo "Conflicting files:"
    git diff --name-only --diff-filter=U | sed 's/^/  /'
    git diff --diff-filter=U | head -n 200
    git rebase --abort
    exit 1
  fi
  base=$newest
  echo "rebased: $(git rev-list --count "refs/upstream-tags/$base..HEAD") commits on $base"
  endgroup
fi

released=$(git tag --points-at HEAD | grep -E "^${base//./\\.}-dither\.[0-9]+$" | sort -V | tail -n1 || true)
if [ -n "$released" ]; then
  echo "HEAD is $released already: nothing to build or release"
  exit 0
fi
last=$(git tag -l "$base-dither.*" | grep -E "^${base//./\\.}-dither\.[0-9]+$" | sed 's/.*-dither\.//' | sort -n | tail -n1 || true)
tag="$base-dither.$((${last:-0} + 1))"
echo "release to make: $tag"

stage "Build $tag from the committed tree, as its consumer will"
# The source exactly as GitHub's tarball of the tag will hold it (git archive), added to the store under the name and
# hash fetchFromGitHub gives it: the consumer file below then builds from this very path, without a download.
git archive --format=tar --prefix=source/ HEAD | tar -x -C "$work"
src=$(nix-store --add-fixed --recursive sha256 "$work/source")
hash=$("${nixcmd[@]}" hash path --sri "$src")
echo "source $src ($hash)"
"$here/consumer.sh" "$tag" "$hash" > "$work/$NAME.nix"
nix-build "$work/$NAME.nix" --arg pkgs "import $here/nixpkgs.nix { }" -o "$work/result"
endgroup

stage "Smoke-test $tag"
nix-build "$here" -A smoke --arg "$NAME" "import $work/$NAME.nix { pkgs = import $here/nixpkgs.nix { }; }" -o "$work/smoke"
"$work/smoke/bin/$NAME-smoke"
endgroup

if [ -n "$dry" ]; then
  echo "DRY_RUN: would push $BRANCH and $tag, and release $tag"
  cp "$work/$NAME.nix" "${DRY_RUN_OUT:-$root}/$NAME.nix" 2>/dev/null || true
  exit 0
fi

stage "Publish $tag"
head=$(git rev-parse HEAD)
remote=$(git ls-remote origin "refs/heads/$BRANCH" | cut -f1)
if [ "$mode" = check ] && [ "$remote" != "$head" ]; then
  echo "$BRANCH has moved on to $remote since this push: that run releases it"
  exit 0
fi
if [ "$mode" = track ] && [ "$remote" != "$start" ]; then
  echo "$BRANCH moved on GitHub during this run ($start -> $remote): not overwriting it"
  exit 1
fi
git tag -a "$tag" -m "$NAME $tag: upstream $base with this fork's commits" "$head"
git push --atomic --force-with-lease="refs/heads/$BRANCH:$remote" origin "$head:refs/heads/$BRANCH" "refs/tags/$tag"

# The hash in the consumer file must be the one of GitHub's tarball of the tag.
url="https://github.com/$FORK/archive/refs/tags/$tag.tar.gz"
got=$("${nixcmd[@]}" hash convert --hash-algo sha256 --to sri "$(nix-prefetch-url --unpack --type sha256 "$url" 2>/dev/null)")
if [ "$got" != "$hash" ]; then
  echo "GitHub's tarball of $tag hashes to $got, not $hash: taking the tag back"
  git push origin ":refs/tags/$tag"
  exit 1
fi

{
  echo "Upstream [$base](https://github.com/$UPSTREAM/releases/tag/$base) with this fork's commits:"
  echo
  for c in $(git rev-list --reverse "refs/upstream-tags/$base..$head"); do
    up=$(git log -1 --format=%B "$c" | sed -n 's/^Upstream: //p' | head -n1)
    echo "- $(git log -1 --format='%s ([%h](https://github.com/'"$FORK"'/commit/%H))' "$c")${up:+ — upstream: $up}"
  done
  echo
  echo "Use \`$NAME.nix\` below: a self-contained nix file that builds this tag (fetchFromGitHub with a fixed hash)."
  echo "Built and smoke-tested against nixpkgs $(grep -o 'archive/[0-9a-f]\{40\}' "$here/nixpkgs.nix" | cut -d/ -f2)."
} > "$work/notes.md"
gh release create "$tag" "$work/$NAME.nix" --repo "$FORK" --title "$tag" --notes-file "$work/notes.md" --verify-tag
echo "released $tag: https://github.com/$FORK/releases/tag/$tag"
endgroup
