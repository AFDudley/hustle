{ dockerTools, pkgs }:

# The Ghidra headless decompiler image. Holds Ghidra (analyzeHeadless) and
# a JDK (Ghidra needs one at run time; nixpkgs' ghidra package does not
# bundle one). container.sh runs analyzeHeadless with DecompileExport.py
# (../scripts/) as a post-script, against a binary and a project
# directory bind-mounted from the host. See ../README.md.
dockerTools.buildLayeredImage {
  name = "localhost/hustle-ghidra";
  tag = "latest";

  contents = [
    pkgs.ghidra
    pkgs.jdk17
    pkgs.bash
    pkgs.coreutils
  ];

  config = {
    Env = [ "PATH=/bin" ];
  };
}
