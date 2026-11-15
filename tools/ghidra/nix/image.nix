{ dockerTools, pkgs }:

# The Ghidra headless decompiler image. Holds Ghidra (analyzeHeadless)
# and a JDK (Ghidra needs one at run time; nixpkgs' ghidra package does
# not bundle one). nixpkgs' ghidra package exposes a GUI launcher
# (`ghidra`) on PATH, not `analyzeHeadless` directly: the real script
# lives at `lib/ghidra/support/analyzeHeadless` inside the package's
# own output, so this wraps it onto `PATH` under the name
# `container.sh` and Ghidra's own documentation both expect.
# container.sh runs it with DecompileExport.py (../scripts/) as a
# post-script, against a binary and a project directory bind-mounted
# from the host. See ../README.md.
let
  analyzeHeadless = pkgs.writeShellScriptBin "analyzeHeadless" ''
    exec ${pkgs.ghidra}/lib/ghidra/support/analyzeHeadless "$@"
  '';
in
dockerTools.buildLayeredImage {
  name = "localhost/hustle-ghidra";
  tag = "latest";

  contents = [
    pkgs.ghidra
    analyzeHeadless
    pkgs.jdk17
    pkgs.bash
    pkgs.coreutils
  ];

  config = {
    Env = [ "PATH=/bin" ];
  };
}
