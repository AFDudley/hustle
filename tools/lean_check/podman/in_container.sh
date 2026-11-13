#!/usr/bin/env bash
# Runs inside localhost/hustle-lean-check, with /work mounted from the
# host's tools/lean_check/. See translate.sh for how this is invoked, and
# ../README.md for what each step found.
set -euo pipefail

# charon's own CLI calls `rustup toolchain install nightly-2026-09-17
# --component rustc-dev,llvm-tools,rust-src,miri` itself, every run,
# regardless of any override or default already set (checked
# directly). This image does not bake that toolchain in (see
# ../README.md, "The container", "Why the Rust toolchain installs at
# run time, not at image build time"), so this line does the same
# install `charon` would do, here, where RUSTUP_HOME is the mounted
# `.scratch/lean_check/cache/rustup`: the first run reaches the
# network, every later run with that cache mount finds it already
# installed and does not.
rustup toolchain install nightly-2026-09-17 \
  --component rustc-dev,llvm-tools,rust-src,miri > /dev/null
rustup default "$LEAN_CHECK_RUST_TOOLCHAIN_NAME" > /dev/null

echo "=== cargo test (rust_proto, independent of Charon/Aeneas) ==="
cd /work/rust_proto
cargo test

echo "=== charon cargo --preset=aeneas ==="
rm -f hustle_core_proto.llbc
charon cargo --preset=aeneas

echo "=== aeneas -backend lean -split-files ==="
rm -rf /work/generated/src/HustleCore
aeneas -backend lean -dest /work/generated/src -subdir HustleCore \
  -split-files hustle_core_proto.llbc
mv /work/generated/src/HustleCore/TypesExternal_Template.lean \
   /work/generated/src/HustleCore/TypesExternal.lean

echo "=== lake update / cache get / build (generated/) ==="
cd /work/generated
lake update
lake exe cache get
lake build
