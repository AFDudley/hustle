#!/usr/bin/env bash
# Build localhost/hustle-rng-diff from this directory's flake.nix and load it
# into podman. The shared steps and their reasons are in tools/nix_image_build.sh.
#
# This tool's Rust crate (rust/Cargo.toml) path-depends on hustle_core
# (port/hustle_core, owned by a different task slice), which lives
# outside this directory. nix_image_build.sh's optional EXTRA_SRC:DEST
# argument stages it alongside this directory's own files, as a sibling
# directory named "hustle_core" inside the flake source (see
# nix/rust.nix).
#
# Usage: tools/rng_diff/build.sh
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$here/../nix_image_build.sh" "$here" localhost/hustle-rng-diff rng_diff \
  "$here/../../port/hustle_core:hustle_core"
