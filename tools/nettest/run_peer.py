#!/usr/bin/env python3
"""In-container launcher for one tools/nettest peer or playback job.

Runs inside a podman container started from localhost/hustle-replay-check
(the tools/replay_check image; see that tool's README.md, "The container").
tools/nettest/run.py (outside the container) starts one of these per
podman container: one for the relay, one for the host peer, one for the
client peer, and (once the match ends) one more to play the result back
offline through tools/replay_check/ReplayCheck.gd.

This script starts Xvfb and then runs one Godot scene with one job file,
reusing tools/replay_check/run.py's own launch code (start_display,
Work, godot_prefix, GODOT_FLAGS, script_errors) unchanged, so there is one
place that knows how to start Godot headless in this image.

Usage:
  run_peer.py --work DIR --scene res://... --job-flag --nt-job= \\
      --job-file PATH --done-prefix NETTEST_DONE --log PATH --xdg DIR \\
      [--timeout SECONDS]
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]


def load_replay_check_run() -> Any:
    """Import tools/replay_check/run.py by path. tools/ is not a package."""
    path = REPO / "tools" / "replay_check" / "run.py"
    spec = importlib.util.spec_from_file_location("replay_check_run", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="prepared project directory (see tools/replay_check/run.py Work)")
    parser.add_argument("--scene", required=True, help="res:// scene path")
    parser.add_argument("--job-flag", required=True, help="e.g. --nt-job= or --rc-job=")
    parser.add_argument("--job-file", type=Path, required=True)
    parser.add_argument("--done-prefix", required=True, help="e.g. NETTEST_DONE or REPLAY_CHECK_DONE")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--xdg", type=Path, required=True, help="user:// data directory; emptied first (see run.py run_job)")
    parser.add_argument("--timeout", type=int, default=None, help="seconds; default tools/replay_check/run.py's JOB_TIMEOUT_S")
    args = parser.parse_args()

    rc = load_replay_check_run()
    rc.start_display()
    work = rc.Work(args.work.resolve())
    if not work.godot.exists():
        print(f"error: {work.godot} does not exist; run inside the tools/replay_check image", file=sys.stderr)
        return 2

    args.xdg.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, XDG_DATA_HOME=str(args.xdg))
    timeout = args.timeout if args.timeout is not None else rc.JOB_TIMEOUT_S

    args.log.parent.mkdir(parents=True, exist_ok=True)
    with args.log.open("w") as fh:
        try:
            result = subprocess.run(
                [
                    *rc.godot_prefix(),
                    str(work.godot),
                    *rc.GODOT_FLAGS,
                    "--path",
                    str(work.project),
                    "--fixed-fps",
                    "60",
                    args.scene,
                    f"{args.job_flag}{args.job_file}",
                ],
                stdout=fh,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run already SIGKILLed Godot. NetPlay.gd and
            # ReplayCheck.gd flush their trace files periodically, not
            # only at the end, so a stall still leaves evidence of how
            # far the match got; run.py reads it from the log file.
            print(f"error: timed out after {timeout}s; log {args.log}", file=sys.stderr)
            return 2

    text = args.log.read_text(errors="replace")
    errors = rc.script_errors(text)
    if errors:
        print(f"error: {len(errors)} script errors; first: {errors[0]}; log {args.log}", file=sys.stderr)
        return 2
    done = re.search(rf"^{re.escape(args.done_prefix)} (.*)$", text, re.M)
    if result.returncode != 0 or done is None:
        print(f"error: exit {result.returncode}, no {args.done_prefix} line; log {args.log}", file=sys.stderr)
        return 2
    print(done.group(0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
