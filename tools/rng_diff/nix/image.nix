{ dockerTools, pkgs, rngDiff }:

# The RNG diff image. Holds the rng_diff binary (built by ./rust.nix from
# the committed Cargo.lock) and Python 3. container.sh runs its Rust
# stage and its comparison stage (compare.py) both in this one image. See
# ../README.md, "The container".
dockerTools.buildLayeredImage {
  name = "localhost/hustle-rng-diff";
  tag = "latest";

  contents = [
    rngDiff
    pkgs.python3
  ];

  config = {
    Env = [ "PATH=/bin" ];
  };
}
