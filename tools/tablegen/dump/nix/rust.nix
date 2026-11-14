{ rustPlatform, src }:

# table_dump: prints every value in `hustle_core::tables` as JSON Lines,
# for tools/tablegen's probe comparison. See ../README.md.
#
# `src` holds two sibling crate directories staged by
# tools/nix_image_build.sh: `rust` (this tool's own crate, table_dump,
# whose `src/main.rs` tools/tablegen/gen.py generates and `git add`
# tracks before this image builds) and `hustle_core` (a copy of
# ../../../../port/hustle_core, including the `tables` module gen.py
# just wrote), so `rust/Cargo.toml` can reach it with the path
# dependency `../hustle_core`.
rustPlatform.buildRustPackage {
  pname = "table_dump";
  version = "0.1.0";
  inherit src;
  cargoRoot = "rust";
  buildAndTestSubdir = "rust";
  cargoLock.lockFile = src + "/rust/Cargo.lock";
  meta.platforms = [ "x86_64-linux" ];
}
