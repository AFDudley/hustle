#!/usr/bin/env bash
# Runs inside localhost/hustle-lean-check, with /work mounted from the
# host's tools/lean_check/. See translate.sh for how this is invoked, and
# ../README.md for what each step found.
set -euo pipefail

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
