# The `dither` branch

This fork's `dither` branch is TigerVNC's latest stable release tag with commits carried on top. w0vncserver, the
Wayland server, needs them to run as a sandbox's live view on a headless sway. The carried commits are:

- **Backports from master.** These are w0vncserver work merged upstream but not yet released: clipboard on wlroots
  (ext-data-control-v1), ext-image-copy-capture with its cursor session, backend detection that survives a missing
  portal, and error handling. Each commit message names its upstream PR.
- **Our fixes for a headless seat.** These give the virtual keyboard a default keymap when the seat has no keyboard,
  keep the seat's keyboard and pointer across capability events, capture the cursor once a pointer exists, and stop
  leaking keymap fds and mappings. [UPSTREAM.md](UPSTREAM.md) drafts them as an upstream PR.
- **This directory and `.github/workflows/dither.yml`.** These hold the tracking, the build, the smoke test and the
  releases.

## Releases

Each release is a tag named `<upstream tag>-dither.<n>`, such as `v1.16.2-dither.1`, on a commit that was built and
smoke-tested. Its GitHub release carries `w0vncserver.nix`, a self-contained nix file that builds that tag. It uses
fetchFromGitHub with a fixed hash and inlines [package.nix](package.nix). Copy it as it is. Its comment names the
nixpkgs revision CI tested it against ([nixpkgs.nix](nixpkgs.nix)).

```
nix-build w0vncserver.nix                      # with <nixpkgs>
nix-build w0vncserver.nix --arg pkgs 'import (fetchTarball ...) { }'
```

## Tracking upstream

[`.github/workflows/dither.yml`](../../.github/workflows/dither.yml) runs [ci.sh](ci.sh) in three cases: daily, on a
manual dispatch, and on every push to `dither`.

1. It finds upstream's newest stable tag, `vX.Y.Z` with `Z` below 80. TigerVNC's `.80` and `.90` tags are betas.
2. On a scheduled or dispatched run, if that tag is newer than the one `dither` is on, it rebases the carried commits
   onto it. Backports the new release already contains drop out by themselves.
3. If HEAD already has a release, it stops there. That makes a quiet day, and the push run the tracker's own push
   starts, cost a minute.
4. It builds the release's `w0vncserver.nix` from `git archive HEAD`, added to the store as fetchFromGitHub would
   fetch it. So it builds exactly what a consumer will build, and the hash is known before anything is pushed.
5. It runs [smoke.py](smoke.py) on that build: headless sway, w0vncserver, the RFB handshake and a framebuffer
   update, a key event, the clipboard both ways, and the cursor.
6. It pushes `dither` and the new tag in one atomic push, with `--force-with-lease` after a rebase. It checks that
   GitHub's tarball of the tag has the same hash, then creates the release.

### How it fails safe

Nothing is pushed until the build and the smoke test pass, so a failing run changes no branch and no tag. That covers
a rebase conflict, a build error and a failed test. Instead the run opens one issue, titled "Upstream tracking
failed", or comments on the one already open. The issue gives the stage and the end of the log. The next good run
closes it.

To fix a conflict, rebase the carried commits onto the new tag by hand and push to `dither`. That push runs the same
build and test, and releases on success.

If GitHub's tarball ever hashed differently from the tested tree, the run would delete the new tag again and fail.

### Pushing

The workflow pushes with a deploy key, stored in the secret `DITHER_DEPLOY_KEY`. `GITHUB_TOKEN` can't push a rebase
that brings changes to upstream's own workflow files. A push made with it would also start no workflow. The run that
pushes has already tested what it pushes, so the push run it starts finds the commit released and stops.

Upstream's own workflows are disabled in this fork. They would build every platform on every push.

## By hand

```
nix-build contrib/dither -A w0vncserver
nix-build contrib/dither -A smoke && ./result/bin/w0vncserver-smoke [--keep]
DRY_RUN=1 contrib/dither/ci.sh track     # everything but the push and the release
```
