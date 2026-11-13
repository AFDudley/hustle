#!/usr/bin/env bash
# Build localhost/hustle-fixedmath-diff from this directory's flake.nix and load it into podman.
# The shared steps and their reasons are in tools/nix_image_build.sh.
#
# Usage: tools/fixedmath_diff/build.sh
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$here/../nix_image_build.sh" "$here" localhost/hustle-fixedmath-diff fixedmath_diff
