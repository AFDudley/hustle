#!/usr/bin/env bash
# Runs the whole tools/lean_check pipeline inside the image
# localhost/hustle-lean-check: Rust tests, Charon extraction, Aeneas
# translation to Lean, and a `lake build` of the result.
#
# Usage: tools/lean_check/podman/translate.sh
#
# ../build.sh builds the image with Nix (see ../README.md, "The
# container") and loads it into podman; podman reuses the named Nix
# store volume between runs, so a rebuild after a small change is
# incremental. The container never touches the host's Rust, Lean or
# Python toolchains. CPU use is capped at 8 cores (the CFS quota below;
# this host has no `cpuset` controller delegated to the user slice, so
# `--cpuset-cpus` fails -- the quota is the working substitute).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LEAN_CHECK_DIR="$(cd "${HERE}/.." && pwd)"
CACHE_DIR="${LEAN_CHECK_DIR}/../../.scratch/lean_check/cache"
mkdir -p "${CACHE_DIR}/mathlib-cache" "${CACHE_DIR}/rustup"

"${LEAN_CHECK_DIR}/build.sh"

# mathlib's prebuilt `.olean` cache (about 9,080 files, order of a
# gigabyte) is the slow part to re-fetch, so it is mounted from the host
# to survive the container's `--rm` between runs. The Lean toolchain
# itself is baked into the image (see ../nix/lean-toolchain.nix), so
# there is no elan toolchain cache to mount for it.
#
# The Rust nightly toolchain is not baked into the image (see
# ../README.md, "The container", "Why the Rust toolchain installs at
# run time, not at image build time"): podman/in_container.sh installs
# it with `rustup` on the first run into this mount, so later runs find
# it already there.
#
# `generated/.lake/` (package clones and build output) is not mounted
# separately: it is already under the main /work mount below and
# persists on the host the same way.
podman run --rm \
  --cpu-period 100000 --cpu-quota 800000 \
  -v "${LEAN_CHECK_DIR}:/work:Z" \
  -v "${CACHE_DIR}/mathlib-cache:/root/.cache/mathlib:Z" \
  -v "${CACHE_DIR}/rustup:/root/.rustup:Z" \
  localhost/hustle-lean-check \
  bash /work/podman/in_container.sh
