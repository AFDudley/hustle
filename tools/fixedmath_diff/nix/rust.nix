{ rustPlatform, src }:

# fixedmath_diff: recomputes every FixedMath call two ways — the fixed,
# fixed-sqrt and fixed_trigonometry crates (binary "fixedmath_diff"), and
# hustle_core::fixed (binary "hustle_core_diff") — for a bit-exact diff
# against the game. See ../README.md, ../rust/src/main.rs and
# ../rust/src/hustle_core_main.rs.
#
# `src` is the whole staged flake directory: tools/nix_image_build.sh's
# TOOL_DIR copy (this directory's own git-tracked files, including
# `rust/`) plus the extra `hustle_core` directory its EXTRA_SRC argument
# stages alongside it (see ../build.sh), a copy of
# ../../../port/hustle_core. So `rust/Cargo.toml` can reach it with the
# path dependency `../hustle_core`, the same pattern tools/rng_diff uses.
# `cargoRoot` and `buildAndTestSubdir` tell buildRustPackage that the
# crate under build (and its Cargo.lock) live in the `rust` subdirectory,
# not at the source root.
#
# Every registry dependency in ../rust/Cargo.lock comes from crates.io (no
# git source), so buildRustPackage fetches and checks each one straight
# from the lockfile; no extra outputHashes entry is needed.
rustPlatform.buildRustPackage {
  pname = "fixedmath_diff";
  version = "0.1.0";
  inherit src;
  cargoRoot = "rust";
  buildAndTestSubdir = "rust";
  cargoLock.lockFile = src + "/rust/Cargo.lock";
  meta.platforms = [ "x86_64-linux" ];
}
