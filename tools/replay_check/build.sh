#!/usr/bin/env bash
# Build localhost/hustle-replay-check from flake.nix and load it into
# podman. See README.md, "The container".
#
# Usage: tools/replay_check/build.sh
#
# Nix never runs on the host. It runs inside a pinned docker.io/nixos/nix
# container, pinned by digest below. The Nix store is the named podman
# volume hustle-nix-store, mounted at /nix, so a rebuild after a small
# change reuses the store and finishes in seconds.
#
# container.sh calls this before every run, so it skips the tarball copy
# and `podman load` (both slow: the image is several GB) when the Nix
# output did not change and podman already has it loaded.
set -euo pipefail

tool_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$tool_dir/../.." && pwd)"
# docker.io/nixos/nix:latest, pinned on 2026-11-12.
nix_image="docker.io/nixos/nix@sha256:b49cb33f3a7e5cf3933ad8774529de0e3807ec5cf54987aa7ccdfb49f2fc55ed"
store_volume="hustle-nix-store"
out_dir="$repo/.scratch/replay_check_nix"
stamp="$out_dir/loaded-store-path"

mkdir -p "$out_dir"
podman volume create --ignore "$store_volume" > /dev/null

# The out-link is a GC root inside the volume, so a garbage collection of
# hustle-nix-store (laconic-tech-ops' weekly nix-store-gc@hustle-nix-store
# timer) keeps the image tarball. The tarball refers to no store paths, so
# its build inputs (GodotSteam, Mesa, ...) are not kept by this root; after
# a collection the next build fetches or rebuilds them. Each build moves the
# link to the new output, so older images become collectable.
store_path=$(podman run --rm \
  -v "$store_volume:/nix" \
  -v "$tool_dir:$tool_dir:ro" \
  -w "$tool_dir" \
  "$nix_image" \
  sh -c 'mkdir -p /nix/var/nix/gcroots/hustle \
    && nix --extra-experimental-features "nix-command flakes" \
      build .#image --print-out-paths --out-link /nix/var/nix/gcroots/hustle/image')

if [[ -f "$stamp" ]] && [[ "$(cat "$stamp")" == "$store_path" ]] \
  && podman image exists localhost/hustle-replay-check; then
  exit 0
fi

# nix build writes the tarball to the Nix store, which lives in the named
# volume. cp -L copies the real bytes to the bind-mounted output
# directory, so the host's podman (outside this container) can read a
# plain file, not a symlink into a store the host cannot see.
podman run --rm \
  -v "$store_volume:/nix" \
  -v "$out_dir:/out" \
  "$nix_image" \
  cp -L "$store_path" /out/image.tar.gz

podman load -i "$out_dir/image.tar.gz"
rm -f "$out_dir/image.tar.gz"
echo "$store_path" > "$stamp"
