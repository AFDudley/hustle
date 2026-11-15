#!/usr/bin/env bash
# Runs a headless Ghidra decompile of one binary's named functions or
# addresses, and writes the decompiled C to a text file. See README.md.
#
# Usage: tools/ghidra/container.sh <binary> <out_file> <name_or_addr> [more...]
#
# <binary> is read-only. <out_file>'s directory is created and mounted
# read-write. The Ghidra project lives in this worktree's
# .scratch/ghidra_project/<binary basename>/, so a second run against
# the same binary reuses the existing auto-analysis (`-process
# -noanalysis` instead of `-import`) instead of re-analyzing a ~5 MB
# binary from scratch every time.
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <binary> <out_file> <name_or_addr> [more...]" >&2
  exit 2
fi

binary="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
out_file="$2"
shift 2
targets=("$@")

tool_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$tool_dir/../.." && pwd)"
image="localhost/hustle-ghidra"

"$tool_dir/build.sh"

prog_name="$(basename "$binary")"
project_dir="$repo/.scratch/ghidra_project"
mkdir -p "$project_dir" "$(dirname "$out_file")"

out_dir="$(cd "$(dirname "$out_file")" && pwd)"
out_name="$(basename "$out_file")"

if [[ -f "$project_dir/ghidra.gpr" ]]; then
  import_args=(-process "$prog_name" -noanalysis)
else
  import_args=(-import "/bin_mount/$prog_name")
fi

podman run --rm --cpu-quota 800000 --cpu-period 100000 \
  -v "$binary:/bin_mount/$prog_name:ro" \
  -v "$project_dir:/project" \
  -v "$tool_dir/scripts:/scripts:ro" \
  -v "$out_dir:/out" \
  "$image" \
  analyzeHeadless /project ghidra \
  "${import_args[@]}" \
  -scriptPath /scripts \
  -postScript DecompileExport.py "/out/$out_name" "${targets[@]}"

echo "wrote $out_file"
