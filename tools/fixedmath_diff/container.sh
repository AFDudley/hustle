#!/usr/bin/env bash
# Runs the FixedMath diff end to end. See README.md.
#
# Usage: tools/fixedmath_diff/container.sh [--work DIR]
#
# Three podman stages, each read-only on the repository:
#   1. The Godot run (tools/replay_check's Nix image): calls every
#      FixedMath method on the probe set, writes godot_out.jsonl.
#   2. The Rust run (this tool's own Nix image, built by build.sh):
#      recomputes with the fixed and fixed-sqrt crates, writes
#      rust_out.jsonl.
#   3. The comparison (this tool's own Nix image again, for its
#      Python 3): lines the two files up and writes report.json.
#
# No stage runs Godot, cargo, or this task's Python directly on the host;
# container.sh itself only invokes podman.
set -euo pipefail

tool_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$tool_dir/../.." && pwd)"
godot_image="localhost/hustle-replay-check"
fixedmath_image="localhost/hustle-fixedmath-diff"
work="${XDG_CACHE_HOME:-$HOME/.cache}/hustle-fixedmath-diff"

while (($#)); do
  case "$1" in
    --work)
      work="$(realpath -m "$2")"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$work/out" "$work/xdg"

echo "--- stage 1: Godot ---"
"$repo/tools/replay_check/build.sh"
podman run --rm --init --cpus 8 \
  --entrypoint bash \
  -v "$repo:/repo:ro" \
  -v "$work/xdg:/xdg" \
  -v "$work/out:/out" \
  -e XDG_DATA_HOME=/xdg \
  -e LP_NUM_THREADS=1 \
  --pids-limit=-1 \
  "$godot_image" \
  -c '
    set -e
    Xvfb -displayfd 3 -screen 0 1280x720x24 -nolisten tcp 3>/tmp/dispfd &
    sleep 1
    export DISPLAY=":$(cat /tmp/dispfd)"
    exec timeout 300 /opt/godotsteam/linux-351-editor.64 --no-window --audio-driver Dummy \
      --fixed-fps 60 --path /repo res://tools/fixedmath_diff/FixedMathDiff.tscn \
      --fmd-out=/out/godot_out.jsonl
  '

echo "--- stage 2: Rust ---"
"$tool_dir/build.sh"
podman run --rm --cpus 8 \
  --entrypoint fixedmath_diff \
  -v "$work/out:/out" \
  "$fixedmath_image" \
  /out/godot_out.jsonl /out/rust_out.jsonl

echo "--- stage 3: compare ---"
podman run --rm --cpus 8 \
  --entrypoint python3 \
  -v "$tool_dir/compare.py:/compare.py:ro" \
  -v "$work/out:/out" \
  "$fixedmath_image" \
  /compare.py /out/godot_out.jsonl /out/rust_out.jsonl /out/report.json
