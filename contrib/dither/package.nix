# w0vncserver (TigerVNC's Wayland server) from this fork's `dither` branch: nixpkgs' tigervnc, built from the given
# source, without the viewer and without the Xvnc that nixpkgs builds from the X server's sources.
#
# A plain callPackage function with no relative imports, so consumer.sh can inline it into the self-contained
# w0vncserver.nix attached to each release.
{ lib, tigervnc, src, version }:
tigervnc.overrideAttrs (old: {
  pname = "w0vncserver";
  inherit src version;
  # The fixes nixpkgs would carry are in the source already.
  patches = [ ];
  cmakeFlags = (old.cmakeFlags or [ ]) ++ [ (lib.cmakeBool "BUILD_VIEWER" false) ];
  # nixpkgs builds and installs Xvnc here; this package is only the Wayland server (and x0vncserver beside it).
  postBuild = "";
  postInstall = "";
  passthru = (old.passthru or { }) // { tests = { }; };
  meta = old.meta // {
    description = "TigerVNC's Wayland VNC server (w0vncserver), with fixes for headless wlroots compositors";
    homepage = "https://github.com/zyansheep/tigervnc/tree/dither";
    platforms = lib.platforms.linux;
    mainProgram = "w0vncserver";
  };
})
