#!/usr/bin/env bash
# Run tools/replay_check/run.py inside its podman container. See README.md.
#
# Usage: tools/replay_check/container.sh [--work DIR] [--gpu CDI_DEVICE] [--jobs N] <command> [args]
#
# --gpu passes one GPU through the NVIDIA container toolkit (CDI), for
# example `--gpu nvidia.com/gpu=GPU-<uuid>` (list them with
# `nvidia-ctk cdi list`). Godot then renders on it through VirtualGL. See
# README.md, "GPU rendering".
#
# Mounts, all at the same absolute path as on the host, so paths in
# arguments and logs mean the same thing on both sides:
#   - the repository, read-only (run.py only reads it),
#   - the work directory (default ~/.cache/hustle-replay-check),
#   - the directory given to each --out, --replays, --golden and --coverage option.
set -euo pipefail

tool_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$tool_dir/../.." && pwd)"
image="localhost/hustle-replay-check"
work="${XDG_CACHE_HOME:-$HOME/.cache}/hustle-replay-check"

args=()
mounts=(-v "$repo:$repo:ro")
gpu=()
while (($#)); do
  case "$1" in
    --work)
      work="$(realpath -m "$2")"
      shift 2
      ;;
    --gpu)
      # VGL_READBACK=none: no tool looks at the Xvfb window, so VirtualGL
      # need not copy each frame there. Godot still reads its own frames
      # from the GPU. __GL_YIELD=USLEEP: the NVIDIA driver sleeps instead
      # of spinning while it waits for the GPU. See README, "GPU rendering".
      gpu=(--device "$2" -e REPLAY_CHECK_GPU=1 -e VGL_READBACK=none -e __GL_YIELD=USLEEP)
      shift 2
      ;;
    --out | --replays | --golden | --coverage)
      dir="$(realpath -m "$2")"
      mkdir -p "$dir"
      mounts+=(-v "$dir:$dir")
      args+=("$1" "$dir")
      shift 2
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done
mkdir -p "$work"
mounts+=(-v "$work:$work")

"$tool_dir/build.sh"
# The entrypoint is /repo/tools/replay_check/run.py. /repo is the same
# read-only checkout, so run.py resolves REPO from its own location.
exec podman run --rm --init "${gpu[@]}" \
  "${mounts[@]}" -v "$repo:/repo:ro" \
  "$image" --work "$work" "${args[@]}"
