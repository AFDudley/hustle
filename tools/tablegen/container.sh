#!/usr/bin/env bash
# Runs the whole table generator end to end. See README.md.
#
# Usage: tools/tablegen/container.sh [--work DIR]
#
# Stages, each in its own podman container:
#   1. Generate: gen.py reads the five characters' and their projectiles'
#      `.tscn`/`.gd` files and writes `port/hustle_core/src/tables/*.rs`,
#      `tools/tablegen/dump/rust/src/main.rs`, and
#      `tools/tablegen/manifest.json`, into a scratch directory.
#   2. This script (plain `cp`, on the host, not a container) copies
#      that output into the real repository tree and `git add`s it, so
#      the next stage's `tools/nix_image_build.sh` (which stages only
#      git-tracked files) can see the generated `table_dump` source and
#      the generated tables it depends on.
#   3. Dump: table_dump (tools/tablegen/dump/) prints every table value
#      as JSON Lines.
#   4. Probe: a headless Godot run (tools/replay_check's Nix image)
#      loads every scene named in the manifest and prints the engine's
#      own values the same way.
#   5. Compare: compare.py checks every field, by key, not by line
#      order (see its own docstring), and writes report.json.
#
# No stage runs Python, Godot, or cargo directly on the host; this
# script itself only invokes podman, git, and plain file commands.
set -euo pipefail

tool_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$tool_dir/../.." && pwd)"
tablegen_image="localhost/hustle-tablegen"
dump_image="localhost/hustle-table-dump"
godot_image="localhost/hustle-replay-check"
work="${XDG_CACHE_HOME:-$HOME/.cache}/hustle-tablegen"

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

mkdir -p "$work/gen_out" "$work/out" "$work/xdg"

echo "--- stage 1: generate ---"
"$tool_dir/build.sh"
rm -rf "$work/gen_out"
mkdir -p "$work/gen_out"
podman run --rm --cpu-quota 800000 --cpu-period 100000 \
  --entrypoint python3 \
  -v "$repo:/repo:ro" \
  -v "$work/gen_out:/gen_out" \
  "$tablegen_image" \
  /repo/tools/tablegen/gen.py /repo /gen_out/tables /gen_out/dump_main.rs /gen_out/manifest.json

echo "--- stage 2: copy generated output into the repository ---"
rm -rf "$repo/port/hustle_core/src/tables"
cp -r "$work/gen_out/tables" "$repo/port/hustle_core/src/tables"
mkdir -p "$repo/tools/tablegen/dump/rust/src"
cp "$work/gen_out/dump_main.rs" "$repo/tools/tablegen/dump/rust/src/main.rs"
cp "$work/gen_out/manifest.json" "$repo/tools/tablegen/manifest.json"
git -C "$repo" add port/hustle_core/src/tables tools/tablegen/dump/rust/src/main.rs tools/tablegen/manifest.json

echo "--- stage 3: dump the generated tables ---"
"$tool_dir/dump/build.sh"
podman run --rm --cpu-quota 800000 --cpu-period 100000 \
  --entrypoint table_dump \
  "$dump_image" \
  > "$work/out/dump_out.jsonl"

echo "--- stage 4: probe the real engine ---"
"$repo/tools/replay_check/build.sh"
podman run --rm --init --cpu-quota 800000 --cpu-period 100000 \
  --entrypoint bash \
  -v "$repo:/repo:ro" \
  -v "$work/xdg:/xdg" \
  -v "$work/out:/out" \
  -v "$repo/tools/tablegen/manifest.json:/manifest.json:ro" \
  -e XDG_DATA_HOME=/xdg \
  -e LP_NUM_THREADS=1 \
  --pids-limit=-1 \
  "$godot_image" \
  -c '
    set -e
    # Xvfb writes its display number to fd 3 when it is ready. Read it
    # from a FIFO, so Godot starts only after the X server accepts
    # clients.
    mkfifo /tmp/dispfd
    Xvfb -displayfd 3 -screen 0 1280x720x24 -nolisten tcp 3>/tmp/dispfd &
    read -r display_number </tmp/dispfd
    export DISPLAY=":$display_number"
    exec timeout 300 /opt/godotsteam/linux-351-editor.64 --no-window --audio-driver Dummy \
      --fixed-fps 60 --path /repo res://tools/tablegen/TableGenProbe.tscn \
      --tablegen-manifest=/manifest.json --tablegen-out=/out/godot_out.jsonl
  '

echo "--- stage 5: compare ---"
podman run --rm --cpu-quota 800000 --cpu-period 100000 \
  --entrypoint python3 \
  -v "$tool_dir/compare.py:/compare.py:ro" \
  -v "$repo/tools/tablegen/manifest.json:/manifest.json:ro" \
  -v "$work/out:/out" \
  "$tablegen_image" \
  /compare.py /out/godot_out.jsonl /out/dump_out.jsonl /manifest.json /out/report.json
