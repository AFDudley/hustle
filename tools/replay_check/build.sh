#!/usr/bin/env bash
# Build localhost/hustle-replay-check from this directory's flake.nix and load it into podman.
# The shared steps and their reasons are in tools/nix_image_build.sh.
#
# Usage: tools/replay_check/build.sh
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$here/../nix_image_build.sh" "$here" localhost/hustle-replay-check replay_check
