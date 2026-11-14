{ rustPlatform, src }:

# fgobject_diff: replays every FGObjectDiff.gd probe and every
# FGObjectRecorder.gd-captured live call against hustle_core::physics,
# and compares the result exactly.
#
# `src` is the whole staged flake directory (tools/nix_image_build.sh's
# TOOL_DIR copy plus the extra `hustle_core` directory its EXTRA_SRC
# argument stages alongside it; see ../build.sh). It holds two sibling
# crate directories, `rust` (this tool's own crate, named
# fgobject_diff) and `hustle_core` (a copy of ../../../port/hustle_core),
# so `rust/Cargo.toml` can reach it with the path dependency
# `../hustle_core`.
rustPlatform.buildRustPackage {
  pname = "fgobject_diff";
  version = "0.1.0";
  inherit src;
  cargoRoot = "rust";
  buildAndTestSubdir = "rust";
  cargoLock.lockFile = src + "/rust/Cargo.lock";
  meta.platforms = [ "x86_64-linux" ];
}
