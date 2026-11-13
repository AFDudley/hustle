#!/usr/bin/env bash
# Runs the whole tools/lean_check pipeline inside the image
# localhost/hustle-lean-check: Rust tests, Charon extraction, Aeneas
# translation to Lean, and a `lake build` of the result.
#
# Usage: tools/lean_check/podman/translate.sh
#
# The container never touches the host's Rust, Lean or Python toolchains.
# CPU use is capped at 8 cores (the CFS quota below; this host has no
# `cpuset` controller delegated to the user slice, so `--cpuset-cpus`
# fails -- the quota is the working substitute).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LEAN_CHECK_DIR="$(cd "${HERE}/.." && pwd)"
CACHE_DIR="${LEAN_CHECK_DIR}/../../.scratch/lean_check/cache"
mkdir -p "${CACHE_DIR}/elan/toolchains" "${CACHE_DIR}/mathlib-cache"

podman build -f "${HERE}/Containerfile" -t localhost/hustle-lean-check "${HERE}"

# The elan toolchain and mathlib's prebuilt `.olean` cache are each
# hundreds of megabytes. Mounting them from the host means a second run
# does not redownload either: `generated/.lake/packages` (git clones) is
# already under the main `/work` mount and persists the same way.
podman run --rm \
  --cpu-period 100000 --cpu-quota 800000 \
  -v "${LEAN_CHECK_DIR}:/work:Z" \
  -v "${CACHE_DIR}/elan/toolchains:/root/.elan/toolchains:Z" \
  -v "${CACHE_DIR}/mathlib-cache:/root/.cache/mathlib:Z" \
  localhost/hustle-lean-check \
  bash /work/podman/in_container.sh
