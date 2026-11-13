{ rustPlatform, src }:

# fixedmath_diff: recomputes every FixedMath call with the fixed,
# fixed-sqrt and fixed_trigonometry crates, for a bit-exact diff against
# the game. See ../README.md and ../rust/src/main.rs. Every dependency in
# ../rust/Cargo.lock comes from crates.io (no git source), so
# buildRustPackage fetches and checks each one straight from the
# lockfile; no extra outputHashes entry is needed.
rustPlatform.buildRustPackage {
  pname = "fixedmath_diff";
  version = "0.1.0";
  inherit src;
  cargoLock.lockFile = src + "/Cargo.lock";
  meta.platforms = [ "x86_64-linux" ];
}
