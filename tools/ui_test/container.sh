#!/usr/bin/env bash
# Run tools/ui_test/run.py inside the tools/replay_check podman container.
# See README.md.
#
# Usage: tools/ui_test/container.sh [--work DIR] [--gpu CDI_DEVICE] [--jobs N] <command> [args]
#
# The image's own entrypoint is tools/replay_check/run.py, so this script
# overrides it with --entrypoint python3 and runs tools/ui_test/run.py
# instead (the same `podman run --entrypoint python3 <image> <script>`
# pattern used elsewhere to run a second script from a shared image).
# Everything else -- mounts, --work default, --gpu, --out/--coverage
# directory handling -- mirrors tools/replay_check/container.sh so a path
# in an argument or a log means the same thing on both sides.
set -euo pipefail

tool_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$tool_dir/../.." && pwd)"
image="localhost/hustle-replay-check"
work="${XDG_CACHE_HOME:-$HOME/.cache}/hustle-ui-test"

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
      gpu=(--device "$2" -e REPLAY_CHECK_GPU=1 -e VGL_READBACK=none -e __GL_YIELD=USLEEP)
      shift 2
      ;;
    --out | --coverage)
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

"$repo/tools/replay_check/build.sh"
exec podman run --rm --init "${gpu[@]}" \
  "${mounts[@]}" -v "$repo:/repo:ro" \
  --entrypoint python3 \
  "$image" /repo/tools/ui_test/run.py --work "$work" "${args[@]}"
