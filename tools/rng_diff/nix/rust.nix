{ rustPlatform, src }:

# rng_diff: recomputes every RngDiff.gd probe with hustle_core's rng.rs
# and godot_hash.rs, for a bit-exact diff against the real game.
#
# `src` is the whole staged flake directory (tools/nix_image_build.sh's
# TOOL_DIR copy plus the extra `hustle_core` directory its EXTRA_SRC
# argument stages alongside it; see ../build.sh). It holds two sibling
# crate directories, `rust` (this tool's own crate, named rng_diff) and
# `hustle_core` (a copy of ../../../port/hustle_core), so `rust/Cargo.toml`
# can reach it with the path dependency `../hustle_core`.
# `cargoRoot` tells the cargo-vendor setup hook where Cargo.toml and
# Cargo.lock actually live (`cargoSetupPostPatchHook` otherwise looks
# for Cargo.lock at the source root and fails: "Missing Cargo.lock from
# src"). `buildAndTestSubdir` then tells buildRustPackage the same
# subdirectory holds the crate to actually build.
rustPlatform.buildRustPackage {
  pname = "rng_diff";
  version = "0.1.0";
  inherit src;
  cargoRoot = "rust";
  buildAndTestSubdir = "rust";
  cargoLock.lockFile = src + "/rust/Cargo.lock";
  meta.platforms = [ "x86_64-linux" ];
}
