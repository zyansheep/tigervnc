# w0vncserver (TigerVNC's Wayland server) from this fork's `dither` branch: nixpkgs' tigervnc, built from the given
# source, without the viewer and without the Xvnc that nixpkgs builds from the X server's sources.
#
# A plain callPackage function with no relative imports, so consumer.sh can inline it into the self-contained
# w0vncserver.nix attached to each release.
{ lib, tigervnc, src, version }:
let
  # nixpkgs' postBuild and postInstall build Xvnc from the X server's sources, install it and wrap its vncserver
  # script; this package has none of that, so it leaves them out. Every line it leaves out must be one of those steps:
  # when a nixpkgs bump adds another, evaluation stops here and names it, rather than the build losing it unseen.
  xvncSteps = [
    ""
    "export (NIX_CFLAGS_COMPILE|CXXFLAGS)=.*"
    "# Build Xvnc"
    "tar xf .*"
    "cp -R xorg.*"
    "pushd unix/xserver.*"
    "version=.*"
    "patch -p1 .*"
    "autoreconf .*"
    "\\./configure .*"
    "--.*"
    "make TIGERVNC_SRC=.*"
    "popd"
    "rm -f \\$out/lib/xorg/protocol\\.txt"
    "wrapProgram \\$out/bin/vncserver .*"
  ];
  leftOut =
    phase: text:
    let
      unknown = lib.filter (l: !lib.any (re: builtins.match re l != null) xvncSteps) (
        map lib.trim (lib.splitString "\n" text)
      );
    in
    if unknown == [ ] then
      ""
    else
      throw "w0vncserver: nixpkgs' tigervnc ${phase} has a step this package does not know, and would leave out unseen: `${lib.head unknown}`. Keep it, or add it to xvncSteps in contrib/dither/package.nix.";
in
tigervnc.overrideAttrs (old: {
  pname = "w0vncserver";
  inherit src version;
  # The fixes nixpkgs would carry are in the source already; a patch nixpkgs adds later must be looked at first.
  patches =
    if (old.patches or [ ]) == [ ] then
      [ ]
    else
      throw "w0vncserver: nixpkgs' tigervnc carries patches now (${toString (map baseNameOf old.patches)}): see whether this source has them, then leave them out here.";
  cmakeFlags = (old.cmakeFlags or [ ]) ++ [ (lib.cmakeBool "BUILD_VIEWER" false) ];
  # nixpkgs builds and installs Xvnc here; this package is only the Wayland server (and x0vncserver beside it).
  postBuild = leftOut "postBuild" (old.postBuild or "");
  postInstall = leftOut "postInstall" (old.postInstall or "");
  passthru = (old.passthru or { }) // { tests = { }; };
  meta = old.meta // {
    description = "TigerVNC's Wayland VNC server (w0vncserver), with fixes for headless wlroots compositors";
    homepage = "https://github.com/zyansheep/tigervnc/tree/dither";
    platforms = lib.platforms.linux;
    mainProgram = "w0vncserver";
  };
})
