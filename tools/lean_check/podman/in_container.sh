#!/usr/bin/env bash
# Runs inside localhost/hustle-lean-check, with /work mounted from the
# host's tools/lean_check/ and /work/hustle_core_src mounted read-only
# from the host's port/hustle_core/. See translate.sh for how this is
# invoked, and ../README.md for what each step found.
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

echo "=== charon cargo --preset=aeneas (rust_proto) ==="
rm -f hustle_core_proto.llbc
charon cargo --preset=aeneas

echo "=== aeneas -backend lean -split-files (rust_proto -> HustleCore) ==="
rm -rf /work/generated/src/HustleCore
aeneas -backend lean -dest /work/generated/src -subdir HustleCore \
  -split-files hustle_core_proto.llbc
mv /work/generated/src/HustleCore/TypesExternal_Template.lean \
   /work/generated/src/HustleCore/TypesExternal.lean

# port/hustle_core: the real port's crate (a different task slice).
# Mounted read-only at /work/hustle_core_src; copied to a writable
# directory under /work (cargo needs to write `target/`, and the mount
# above is read-only). /work itself persists on the host between runs
# (translate.sh's main bind mount), so this copy does too.
echo "=== cargo test (port/hustle_core, independent of Charon/Aeneas) ==="
rm -rf /work/hustle_core_build
cp -r /work/hustle_core_src /work/hustle_core_build
cd /work/hustle_core_build
cargo test

echo "=== charon cargo --preset=aeneas (port/hustle_core) ==="
rm -f hustle_core.llbc
charon cargo --preset=aeneas

echo "=== aeneas -backend lean -split-files (port/hustle_core -> HustleCorePort) ==="
rm -rf /work/generated/src/HustleCorePort
aeneas -backend lean -dest /work/generated/src -subdir HustleCorePort \
  -split-files hustle_core.llbc
if [ -f /work/generated/src/HustleCorePort/TypesExternal_Template.lean ]; then
  mv /work/generated/src/HustleCorePort/TypesExternal_Template.lean \
     /work/generated/src/HustleCorePort/TypesExternal.lean
fi
if [ -f /work/generated/src/HustleCorePort/FunsExternal_Template.lean ]; then
  mv /work/generated/src/HustleCorePort/FunsExternal_Template.lean \
     /work/generated/src/HustleCorePort/FunsExternal.lean
fi
ls /work/generated/src/HustleCorePort

echo "=== lake update / cache get / build (generated/) ==="
cd /work/generated
lake update
lake exe cache get
lake build
