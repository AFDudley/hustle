{ dockerTools, pkgs, fixedParse }:

# The table generator image. Holds the fixed_parse binary (built by
# ./rust.nix from the committed Cargo.lock) and Python 3, so gen.py can
# shell out to fixed_parse for every fixed-point table value. See
# ../README.md, "The container".
dockerTools.buildLayeredImage {
  name = "localhost/hustle-tablegen";
  tag = "latest";

  contents = [
    fixedParse
    pkgs.python3
  ];

  config = {
    Env = [ "PATH=/bin" ];
  };
}
