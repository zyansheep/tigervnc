# Build and smoke-test w0vncserver from this checkout, against the pinned nixpkgs:
#   nix-build contrib/dither -A w0vncserver
#   nix-build contrib/dither -A smoke && ./result/bin/w0vncserver-smoke
# To test another build of it (a release's w0vncserver.nix, say), pass it in:
#   nix-build contrib/dither -A smoke --arg w0vncserver 'import ./w0vncserver.nix { pkgs = import ./contrib/dither/nixpkgs.nix { }; }'
{
  pkgs ? import ./nixpkgs.nix { },
  # The committed tree (what `git archive` and GitHub's tarballs hold), named as fetchFromGitHub names its output.
  src ? builtins.fetchGit { url = ../..; name = "source"; },
  version ? "dev",
  w0vncserver ? pkgs.callPackage ./package.nix { inherit src version; },
}:
{
  inherit w0vncserver;
  smoke = pkgs.writeShellApplication {
    name = "w0vncserver-smoke";
    runtimeInputs = [ w0vncserver ] ++ (with pkgs; [ sway wev wl-clipboard python3 coreutils ]);
    text = ''exec python3 -I ${./smoke.py} "$@"'';
  };
}
