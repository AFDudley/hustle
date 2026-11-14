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
# tools/tablegen/gen.py writes this file (one Charon CLI token per
# line): it marks every table data function and large const opaque,
# and keeps the table's own types transparent. Without it, Aeneas's
# translation of the tables module alone took thousands of seconds
# (see tools/lean_check/README.md, "port/hustle_core, the tables
# slice", for the measured counts before and after).
mapfile -t TABLEGEN_OPAQUE_ARGS < /work/generated/opaque_patterns.txt
charon cargo --preset=aeneas "${TABLEGEN_OPAQUE_ARGS[@]}"

echo "=== aeneas -backend lean -split-files (port/hustle_core -> HustleCorePort) ==="
rm -rf /work/generated/src/HustleCorePort
aeneas -backend lean -dest /work/generated/src -subdir HustleCorePort \
  -split-files hustle_core.llbc
if [ -f /work/generated/src/HustleCorePort/TypesExternal_Template.lean ]; then
  mv /work/generated/src/HustleCorePort/TypesExternal_Template.lean \
     /work/generated/src/HustleCorePort/TypesExternal.lean
fi
if [ -f /work/generated/src/HustleCorePort/FunsExternal_Template.lean ]; then
  # tools/tablegen/gen.py writes a real Lean `def` for every item this
  # template would otherwise axiomatize (see ../README.md, "port/
  # hustle_core, the tables slice"). Drop Aeneas's own template and
  # copy in the committed replacement instead of renaming it: the
  # replacement lives at /work/generated/HustleCorePortFunsExternal.lean,
  # outside this directory, specifically so this `rm -rf` never deletes it.
  rm /work/generated/src/HustleCorePort/FunsExternal_Template.lean
  cp /work/generated/HustleCorePortFunsExternal.lean \
     /work/generated/src/HustleCorePort/FunsExternal.lean
fi
ls /work/generated/src/HustleCorePort

echo "=== check no axiom or sorry remains in HustleCorePort ==="
if grep -rEwn 'axiom|sorry' /work/generated/src/HustleCorePort; then
  echo "found axiom or sorry in HustleCorePort (lines above); fix before building" >&2
  exit 1
fi

echo "=== lake update / cache get / build (generated/) ==="
cd /work/generated
lake update
lake exe cache get
lake build

echo "=== build + run table_dump (Rust side of the equality check) ==="
# table_dump's Cargo.toml path dependency is "../hustle_core" (the same
# layout tools/nix_image_build.sh stages for the hustle-table-dump
# image; see tools/tablegen/README.md, "The container"). Mirror that
# layout here with a symlink, so the same Cargo.toml works unmodified.
rm -rf /work/table_dump_build
cp -r /work/table_dump_src /work/table_dump_build
ln -sfn /work/hustle_core_build /work/hustle_core
cd /work/table_dump_build
cargo build --release
./target/release/table_dump > /work/rust_table_dump.jsonl

echo "=== run the Lean TableDump executable and compare (equality check) ==="
cd /work/generated
lake exe TableDump > /work/lean_table_dump.jsonl
cmp /work/rust_table_dump.jsonl /work/lean_table_dump.jsonl
echo "table dumps match: $(wc -l < /work/rust_table_dump.jsonl) lines"
