#!/usr/bin/env bash
# Run tools/covsearch/run.py inside the tools/replay_check podman image.
# See tools/covsearch/README.md.
#
# Usage: tools/covsearch/container.sh [--work DIR] <command> [args]
#
# Reuses localhost/hustle-replay-check (built by tools/replay_check/build.sh):
# same GodotSteam build, same X11/audio setup, same project-copy mechanics.
# Only the entry point (tools/covsearch/run.py) and the scene (CovWorker.gd)
# differ; see run.py's module docstring.
set -euo pipefail

tool_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$tool_dir/../.." && pwd)"
image="localhost/hustle-replay-check"
work="${XDG_CACHE_HOME:-$HOME/.cache}/hustle-covsearch"
# The five Ninja-mirror roots (design step 3) live in the main checkout's
# .scratch, not this worktree. Mount it read-only alongside the worktree.
baseline="/home/rix/code/git_puller/repos/hustle/.scratch/coverage_baseline"

args=()
mounts=(-v "$repo:$repo:ro")
if [[ -d "$baseline" ]]; then
  mounts+=(-v "$baseline:$baseline:ro")
fi
while (($#)); do
  case "$1" in
    --work)
      work="$(realpath -m "$2")"
      shift 2
      ;;
    --roots | --corpus)
      target="$(realpath -m "$2")"
      mkdir -p "$(dirname "$target")"
      mounts+=(-v "$(dirname "$target"):$(dirname "$target")")
      args+=("$1" "$target")
      shift 2
      ;;
    --out | --run | --seed-run)
      # A directory, mounted itself: its parent can be the repository, which
      # is mounted read-only (for example `report --out tests/`).
      target="$(realpath -m "$2")"
      mkdir -p "$target"
      mounts+=(-v "$target:$target")
      args+=("$1" "$target")
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

"$tool_dir/../replay_check/build.sh"
# README.md is mounted a second time, read-write, at its own path inside
# the /repo mount -- the one file a running "coordinate" needs to write,
# so it can checkpoint live progress into it every 90s (see
# coordinator.py _update_readme_progress and README.md "Live progress").
# Every other file stays under the read-only /repo mount above: this does
# not reopen the "never modify committed game files" rule, it narrows one
# documentation file's own mount from read-only to read-write.
exec podman run --rm --init --cpu-quota 800000 --cpu-period 100000 --pids-limit=-1 \
  -e LP_NUM_THREADS=1 \
  --entrypoint python3 \
  "${mounts[@]}" -v "$repo:/repo:ro" \
  -v "$tool_dir/README.md:/repo/tools/covsearch/README.md" \
  "$image" /repo/tools/covsearch/run.py --work "$work" "${args[@]}"
