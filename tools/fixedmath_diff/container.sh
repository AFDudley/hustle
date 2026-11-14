#!/usr/bin/env bash
# Runs the FixedMath diff end to end. See README.md.
#
# Usage: tools/fixedmath_diff/container.sh [--work DIR]
#
# Three podman stages, each read-only on the repository:
#   1. The Godot run (tools/replay_check's Nix image): calls every
#      FixedMath method on the probe set, writes godot_out.jsonl.
#   2. The Rust run (this tool's own Nix image, built by build.sh): two
#      binaries from the same image, recomputing the same probe set —
#      "fixedmath_diff" with the fixed and fixed-sqrt crates
#      (rust_out.jsonl), and "hustle_core_diff" with
#      port/hustle_core::fixed (hustle_core_out.jsonl).
#   3. The comparison (this tool's own Nix image again, for its
#      Python 3): three pairwise diffs — game vs. the crate reference,
#      game vs. hustle_core, and the crate reference vs. hustle_core
#      (these two Rust outputs must be identical line by line).
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
    # Xvfb writes its display number to fd 3 when it is ready. Read it
    # from a FIFO, so Godot starts only after the X server accepts
    # clients. A plain `sleep 1` raced under host load ("X11 Display is
    # not available"); tools/rng_diff/container.sh found the same bug.
    mkfifo /tmp/dispfd
    Xvfb -displayfd 3 -screen 0 1280x720x24 -nolisten tcp 3>/tmp/dispfd &
    read -r display_number </tmp/dispfd
    export DISPLAY=":$display_number"
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

podman run --rm --cpu-quota 800000 --cpu-period 100000 \
  --entrypoint hustle_core_diff \
  -v "$work/out:/out" \
  "$fixedmath_image" \
  /out/godot_out.jsonl /out/hustle_core_out.jsonl

echo "--- stage 3: compare ---"
# game vs. the fixed/fixed-sqrt/fixed_trigonometry crates: known
# mismatches exist (angle_dist, sign, and the two dead methods;
# README.md). Exit code captured, not fatal here.
podman run --rm --cpus 8 \
  --entrypoint python3 \
  -v "$tool_dir/compare.py:/compare.py:ro" \
  -v "$work/out:/out" \
  "$fixedmath_image" \
  /compare.py /out/godot_out.jsonl /out/rust_out.jsonl /out/report.json \
  && rust_vs_game=0 || rust_vs_game=$?

# game vs. hustle_core: the same known mismatches, carried over, not
# introduced by the port.
podman run --rm --cpu-quota 800000 --cpu-period 100000 \
  --entrypoint python3 \
  -v "$tool_dir/compare.py:/compare.py:ro" \
  -v "$work/out:/out" \
  "$fixedmath_image" \
  /compare.py /out/godot_out.jsonl /out/hustle_core_out.jsonl /out/report_hustle_core.json \
  && hustle_core_vs_game=0 || hustle_core_vs_game=$?

# the fixed-crate reference vs. hustle_core: the acceptance check. Every
# one of the 32,529 calls must match exactly; this is the one exit code
# that fails the build.
podman run --rm --cpu-quota 800000 --cpu-period 100000 \
  --entrypoint python3 \
  -v "$tool_dir/compare.py:/compare.py:ro" \
  -v "$work/out:/out" \
  "$fixedmath_image" \
  /compare.py /out/rust_out.jsonl /out/hustle_core_out.jsonl /out/report_hustle_core_vs_fixed.json \
  && hustle_core_vs_rust=0 || hustle_core_vs_rust=$?

echo "--- summary ---"
echo "game vs. fixed-crate reference: exit $rust_vs_game (report.json; known mismatches expected)"
echo "game vs. hustle_core: exit $hustle_core_vs_game (report_hustle_core.json; the same known mismatches expected)"
echo "fixed-crate reference vs. hustle_core: exit $hustle_core_vs_rust (report_hustle_core_vs_fixed.json; must be 0)"
exit "$hustle_core_vs_rust"
