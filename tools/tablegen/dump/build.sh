#!/usr/bin/env bash
# Build localhost/hustle-table-dump from this directory's flake.nix and
# load it into podman. The shared steps and their reasons are in
# tools/nix_image_build.sh.
#
# This crate's `src/main.rs` is generated code (see ../gen.py) that
# names every table in `port/hustle_core/src/tables`. Run ../container.sh
# (which regenerates and `git add`s both before calling this script),
# not this script alone, on a checkout where the tables are stale or
# missing.
#
# Usage: tools/tablegen/dump/build.sh
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$here/../../nix_image_build.sh" "$here" localhost/hustle-table-dump table_dump \
  "$here/../../../port/hustle_core:hustle_core"
