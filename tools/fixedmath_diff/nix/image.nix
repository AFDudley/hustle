{ dockerTools, pkgs, fixedmathDiff }:

# The FixedMath diff image. Holds the fixedmath_diff and hustle_core_diff
# binaries (built by ./rust.nix from the committed Cargo.lock) and
# Python 3. container.sh runs its Rust stage and its comparison stage
# (compare.py) both in this one image. See ../README.md, "The container".
dockerTools.buildLayeredImage {
  name = "localhost/hustle-fixedmath-diff";
  tag = "latest";

  contents = [
    fixedmathDiff
    pkgs.python3
  ];

  config = {
    Env = [ "PATH=/bin" ];
  };
}
