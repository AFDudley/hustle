#!/usr/bin/env python3
"""Plays each replay in a directory through the live path, with
FGObjectRecorder wired in as `chara`, and writes one JSONL file per
replay. See tools/fgobject_diff/README.md, "Recording real play".

This reuses tools/replay_check/run.py: `Work`, `start_display`,
`prepare` (rsync plus asset import), `live_job`, and `run_job`. It
does not change that file. The one patch is local to this script: the
rsynced work copy's own `obj/BaseObj.gd` gets one line replaced,
before any Godot process starts, so `var chara` resolves to the
recording proxy instead of the real native class. The committed
`obj/BaseObj.gd` is never touched.

Usage (inside tools/replay_check's image, with REPLAY_CHECK_CONTAINER=1):
  driver_live.py --work DIR --replays DIR --out DIR
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
REPO = TOOL_DIR.parent.parent
sys.path.insert(0, str(REPO / "tools" / "replay_check"))
import run as rc  # noqa: E402

PATCH_OLD = "var chara = FGObject.new()"
PATCH_NEW = 'var chara = preload("res://tools/fgobject_diff/FGObjectRecorder.gd").new()'


def patch_base_obj(work: "rc.Work") -> None:
    """Replaces the one line in the work copy's obj/BaseObj.gd that
    constructs `chara`. Fails loudly if the line moved or changed, so
    a silent mismatch never hides a skipped patch."""
    path = work.project / "obj" / "BaseObj.gd"
    text = path.read_text()
    count = text.count(PATCH_OLD)
    if count != 1:
        raise rc.CheckError(
            f"{path}: expected exactly one occurrence of {PATCH_OLD!r}, found {count}"
        )
    path.write_text(text.replace(PATCH_OLD, PATCH_NEW))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--replays", type=Path, required=True, help="directory of .replay files")
    parser.add_argument("--out", type=Path, required=True, help="directory for <name>.jsonl files")
    args = parser.parse_args()

    if os.environ.get("REPLAY_CHECK_CONTAINER") != "1":
        print(
            "error: driver_live.py runs only inside tools/replay_check's container",
            file=sys.stderr,
        )
        return 2

    work = rc.Work(args.work.resolve())
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        rc.start_display()
        rc.prepare(work)
        patch_base_obj(work)
    except rc.CheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    files = sorted(args.replays.glob("*.replay"))
    if not files:
        print(f"error: no .replay files in {args.replays}", file=sys.stderr)
        return 2

    failures: list[str] = []
    for replay in files:
        name = replay.stem
        out_file = (args.out / f"{name}.jsonl").resolve()
        out_file.write_text("")
        trace = work.jobs / f"fgobject_{name}.trace"
        job = rc.live_job(replay, trace)
        os.environ["FGOBJECT_DIFF_OUT"] = str(out_file)
        try:
            rc.run_job(work, f"fgobject_{name}", job)
            size = out_file.stat().st_size
            lines = sum(1 for line in out_file.read_text().splitlines() if line)
            print(f"{name}: live replay ok, {lines} calls recorded ({size} bytes)")
        except rc.CheckError as exc:
            failures.append(f"{name}: {exc}")
            print(f"{name}: FAILED: {exc}", file=sys.stderr)

    if failures:
        print(f"{len(failures)} of {len(files)} replays failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
