#!/usr/bin/env bash
# Runs the FGObject diff end to end. See README.md.
#
# Usage: tools/fgobject_diff/container.sh [--work DIR] [--census-replays DIR]
#
# Three podman stages, each read-only on the repository (the two
# writable areas are the work directory and the output directory):
#   1. The Godot edge-value probe (tools/replay_check's Nix image):
#      FGObjectDiff.gd drives FGObjectRecorder through every one of
#      FGObject's 41 methods with edge-value arguments. Writes
#      out/probe.jsonl.
#   2. The Godot live-replay recording (the same image): driver_live.py
#      plays each .replay file in --census-replays through the live
#      path (tools/replay_check's own `live` mode), with
#      FGObjectRecorder wired in as every object's `chara`. Writes one
#      out/live_<name>.jsonl per replay.
#   3. The Rust replay-and-compare (this tool's own Nix image, built
#      by build.sh): replays every recorded call against
#      hustle_core::physics and compares the result exactly. Writes
#      out/report.json and exits 1 if any call mismatched.
# No stage runs Godot, cargo, or this task's Python directly on the
# host; container.sh itself only invokes podman.
set -euo pipefail

tool_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$tool_dir/../.." && pwd)"
godot_image="localhost/hustle-replay-check"
fgobject_image="localhost/hustle-fgobject-diff"
work="${XDG_CACHE_HOME:-$HOME/.cache}/hustle-fgobject-diff"
census_replays="$repo/.scratch/census_live/replays"

while (($#)); do
  case "$1" in
    --work)
      work="$2"
      shift 2
      ;;
    --census-replays)
      census_replays="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "$census_replays" ]]; then
  echo "error: no census replay directory at $census_replays" >&2
  echo "see docs/labbook/2026-11-13-core-game-scope-five-characters.md, decision 6, and this tool's README.md" >&2
  exit 2
fi

# The recorder appends to its output files (several objects share one file),
# so a run must start from an empty out/. Otherwise probe.jsonl keeps the
# calls of every earlier run and the counts grow by one probe set per run.
rm -rf "$work/out"
mkdir -p "$work/out" "$work/xdg" "$work/godot_project/xdg"

echo "--- stage 1: Godot edge-value probe ---"
"$repo/tools/replay_check/build.sh"
podman run --rm --init --cpu-quota 800000 --cpu-period 100000 \
  --entrypoint bash \
  -v "$repo:/repo:ro" \
  -v "$work/xdg:/xdg" \
  -v "$work/out:/out" \
  -e XDG_DATA_HOME=/xdg \
  -e LP_NUM_THREADS=1 \
  -e FGOBJECT_DIFF_OUT=/out/probe.jsonl \
  --pids-limit=-1 \
  "$godot_image" \
  -c '
    set -e
    mkfifo /tmp/dispfd
    Xvfb -displayfd 3 -screen 0 1280x720x24 -nolisten tcp 3>/tmp/dispfd &
    read -r display_number </tmp/dispfd
    export DISPLAY=":$display_number"
    touch /out/probe.jsonl
    timeout 300 /opt/godotsteam/linux-351-editor.64 --no-window --audio-driver Dummy \
      --fixed-fps 60 --path /repo res://tools/fgobject_diff/FGObjectDiff.tscn \
      > /out/probe.log 2>&1
  '
probe_calls=$(grep -c . "$work/out/probe.jsonl" || true)
echo "probe: $probe_calls calls recorded"

echo "--- stage 2: Godot live-replay recording ---"
podman run --rm --init --cpu-quota 800000 --cpu-period 100000 \
  --entrypoint bash \
  -v "$repo:/repo:ro" \
  -v "$work/godot_project:/work" \
  -v "$work/out:/out" \
  -v "$census_replays:/replays:ro" \
  -e REPLAY_CHECK_CONTAINER=1 \
  -e XDG_DATA_HOME=/work/xdg \
  -e LP_NUM_THREADS=1 \
  --pids-limit=-1 \
  "$godot_image" \
  -c '
    set -e
    mkfifo /tmp/dispfd
    Xvfb -displayfd 3 -screen 0 1280x720x24 -nolisten tcp 3>/tmp/dispfd &
    read -r display_number </tmp/dispfd
    export DISPLAY=":$display_number"
    python3 /repo/tools/fgobject_diff/driver_live.py \
      --work /work --replays /replays --out /out
  '

echo "--- stage 3: Rust replay and compare ---"
"$tool_dir/build.sh"
inputs=("/out/probe.jsonl")
shopt -s nullglob
for f in "$work"/out/*.jsonl; do
  base="$(basename "$f")"
  if [[ "$base" != "probe.jsonl" ]]; then
    inputs+=("/out/$base")
  fi
done
shopt -u nullglob

podman run --rm --cpu-quota 800000 --cpu-period 100000 \
  --entrypoint fgobject_diff \
  -v "$work/out:/out" \
  "$fgobject_image" \
  /out/report.json "${inputs[@]}" \
  && status=0 || status=$?

echo "--- summary ---"
echo "report: $work/out/report.json"
exit "$status"
