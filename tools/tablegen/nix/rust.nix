{ rustPlatform, src }:

# fixed_parse: parses decimal strings with hustle_core::fixed::Fixed's own
# rounding rule, for gen.py's fixed-point table values. See ../README.md.
#
# `src` holds two sibling crate directories staged by
# tools/nix_image_build.sh: `rust` (this tool's own crate, fixed_parse)
# and `hustle_core` (a copy of ../../../port/hustle_core), so
# `rust/Cargo.toml` can reach it with the path dependency `../hustle_core`.
rustPlatform.buildRustPackage {
  pname = "fixed_parse";
  version = "0.1.0";
  inherit src;
  cargoRoot = "rust";
  buildAndTestSubdir = "rust";
  cargoLock.lockFile = src + "/rust/Cargo.lock";
  meta.platforms = [ "x86_64-linux" ];
}
