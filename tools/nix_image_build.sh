#!/usr/bin/env bash
# Build one tool's container image from its flake.nix (attribute .#image)
# and load it into podman. Each tool's build.sh calls this with its own
# three values. See tools/replay_check/README.md, "The container".
#
# Usage: tools/nix_image_build.sh TOOL_DIR IMAGE NAME
#   TOOL_DIR  the tool directory that holds flake.nix
#   IMAGE     the podman image the flake builds, e.g. localhost/hustle-replay-check
#   NAME      a short unique name: the GC root /nix/var/nix/gcroots/hustle/NAME
#             and the output directory .scratch/NAME_nix (stamp file inside)
#
# Nix never runs on the host. It runs inside a pinned docker.io/nixos/nix
# container, pinned by digest below. The Nix store is the named podman
# volume hustle-nix-store, mounted at /nix, so a rebuild after a small
# change reuses the store and finishes in seconds.
#
# The tools call their build.sh before every run, so this skips the
# tarball copy and `podman load` (both slow: images are several GB) when
# the Nix output did not change and podman already has the image.
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 TOOL_DIR IMAGE NAME" >&2
  exit 2
fi
tool_dir="$(cd "$1" && pwd)"
image="$2"
name="$3"

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# docker.io/nixos/nix:latest, pinned on 2026-11-12.
nix_image="docker.io/nixos/nix@sha256:b49cb33f3a7e5cf3933ad8774529de0e3807ec5cf54987aa7ccdfb49f2fc55ed"
store_volume="hustle-nix-store"
out_dir="$repo/.scratch/${name}_nix"
stamp="$out_dir/loaded-store-path"
stage="$out_dir/flake-src"

mkdir -p "$out_dir"
podman volume create --ignore "$store_volume" > /dev/null

# The flake source is a copy of the tool directory's git-tracked files
# only. Nix copies a path flake whole into the store on every evaluation,
# and a tool directory holds build outputs: tools/lean_check held 2.6 GB of
# generated/.lake, which every build copied. A file the flake needs must
# therefore be tracked (git add) before a build sees it, as with a git
# flake. The copy is rebuilt on every run so it never goes stale.
rm -rf "$stage"
mkdir -p "$stage"
(cd "$tool_dir" && git ls-files -z --cached -- . | tar --null -T - -cf -) | tar -xf - -C "$stage"
if [[ ! -f "$stage/flake.nix" ]]; then
  echo "$tool_dir/flake.nix is not tracked by git; git add it first" >&2
  exit 1
fi

# The out-link is a GC root inside the volume, so a garbage collection of
# hustle-nix-store (laconic-tech-ops' weekly nix-store-gc@hustle-nix-store
# timer) keeps the image tarball. The tarball refers to no store paths, so
# its build inputs are not kept by this root; after a collection the next
# build fetches or rebuilds them. Each build moves the link to the new
# output, so older images become collectable.
store_path=$(podman run --rm \
  -v "$store_volume:/nix" \
  -v "$stage:$stage:ro" \
  -w "$stage" \
  "$nix_image" \
  sh -c "mkdir -p /nix/var/nix/gcroots/hustle \
    && nix --extra-experimental-features 'nix-command flakes' \
      build .#image --print-out-paths --out-link /nix/var/nix/gcroots/hustle/$name")

if [[ -f "$stamp" ]] && [[ "$(cat "$stamp")" == "$store_path" ]] \
  && podman image exists "$image"; then
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
