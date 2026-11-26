# The nixpkgs this fork's CI builds and tests against (nixos-unstable). Bump both lines together:
#   nix-prefetch-url --unpack https://github.com/NixOS/nixpkgs/archive/<rev>.tar.gz
import (builtins.fetchTarball {
  url = "https://github.com/NixOS/nixpkgs/archive/34ab99075ac4f7e40cf037eef32cb1c360bb85e9.tar.gz";
  sha256 = "1fnxl55lirbikawnajzmripav59mjv7syh675fc68yzfd99nhzc6";
})
