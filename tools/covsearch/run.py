#!/usr/bin/env python3
"""Container entry point for tools/covsearch. See tools/covsearch/README.md.

Reuses tools/replay_check's container image and its project-copy and
asset-import helpers (`copy_project`, `import_assets`, `godot_prefix`,
`Work`): this tool needs the same Godot build, the same X11/audio setup
and the same project copy mechanics, just a different scene and a
different instrumenter. Only this tool's SCENE and instrument.py differ.

Commands:
  prepare         Copy the repo into the work directory, instrument the
                  in-scope files (tools/covsearch/instrument.py) and
                  import the assets.
  worker          Run one CovWorker.gd job (one root) and print its
                  output path. Used directly for Step B and by coordinate.py.
  coordinate      Run the search coordinator (coordinator.py) with up to
                  --jobs worker processes.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
REPO = TOOL_DIR.parent.parent
sys.path.insert(0, str(REPO / "tools" / "replay_check"))
import run as rc_run  # noqa: E402  (tools/replay_check/run.py)

sys.path.insert(0, str(TOOL_DIR))
import instrument as cs_instrument  # noqa: E402

SCENE = "res://tools/covsearch/CovWorker.tscn"
# A normal job (list_candidates or one branch_batch pair) observed in this
# session: 9 to 20 seconds. tools/replay_check's own JOB_TIMEOUT_S (900s)
# is sized for a full generated match, not one decision point here; using
# it as this tool's own per-job budget would let one stalled job block the
# search for 15 minutes. `WorkerStalled` lets a caller (coordinator.run,
# scan_roots) skip or salvage that one stalled job with a named reason
# instead of the whole run hanging or crashing (README.md "Known defect").
JOB_TIMEOUT_S = 150


class WorkerStalled(Exception):
    """A worker job did not finish within JOB_TIMEOUT_S. See the constant's
    own comment: this is a named, surfaced stall, not a silently swallowed
    error -- the caller decides what the stalled node means for the search."""


def prepare(work: rc_run.Work) -> dict:
    if not work.godot.exists():
        raise rc_run.CheckError(f"{work.godot} does not exist; run inside the container")
    rc_run.copy_project(work)
    cov_map = cs_instrument.instrument(work.project)
    cs_instrument.write_map(cov_map, work.root / "probe_map.json")
    rc_run.import_assets(work)
    print(f"prepared {work.project}: {cov_map['size']} probes over {len(cov_map['files'])} files")
    return cov_map


def run_worker(work: rc_run.Work, name: str, job: dict, job_timeout: float = JOB_TIMEOUT_S) -> Path:
    """Run one CovWorker.gd job. Returns the path to its JSONL output.

    Raises WorkerStalled if the Godot process does not exit within
    `job_timeout` seconds; the process is killed first (subprocess.run's
    own behaviour on a timeout), so no Godot process outlives this call.
    """
    work.jobs.mkdir(parents=True, exist_ok=True)
    job_out = work.jobs / f"{name}.jsonl"
    job["out"] = str(job_out)
    job_file = work.jobs / f"{name}.job.json"
    job_file.write_text(json.dumps(job))
    log = work.jobs / f"{name}.log"
    xdg = work.jobs / f"{name}.xdg"
    if xdg.exists():
        import shutil

        shutil.rmtree(xdg)
    env = dict(os.environ, XDG_DATA_HOME=str(xdg))
    with log.open("w") as fh:
        try:
            result = subprocess.run(
                [
                    *rc_run.godot_prefix(),
                    str(work.godot),
                    *rc_run.GODOT_FLAGS,
                    "--path",
                    str(work.project),
                    "--fixed-fps",
                    "60",
                    SCENE,
                    f"--rc-job={job_file}",
                ],
                env=env,
                stdout=fh,
                stderr=subprocess.STDOUT,
                timeout=job_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkerStalled(f"worker job {name} did not finish within {job_timeout}s; see {log}") from exc
    text = log.read_text()
    if "SCRIPT ERROR" in text or "COVSEARCH_FAIL" in text:
        raise rc_run.CheckError(f"worker job {name} failed; see {log}")
    if result.returncode != 0:
        raise rc_run.CheckError(f"worker job {name} exited {result.returncode}; see {log}")
    return job_out


def scan_roots(work: rc_run.Work, replays: list[str], out_path: Path, roots_per_mirror: int) -> None:
    """Run replay_scan for each mirror, then write roots.json.

    Design step 3: the match-start root, plus up to `roots_per_mirror`
    *genuine* decision points of each mirror (CovWorker.gd `_scan_replay`
    only records a tick where some player is actually interruptable, not
    just wherever the scan happened to stop).

    `replay_scan` plays a mirror all the way to its own last recorded
    tick, live, decision by decision. One root this session,
    `replays/A/Ninja_Ninja_1.replay`, reliably runs the single Godot
    process at 85-100% CPU with no further output starting around tick
    706 -- confirmed this session to be specific to driving this replay
    through this tool's own instrumented project, not to the replay or
    the game code: a fresh, uninstrumented `tools/replay_check` "live"
    run of the same replay completes the whole match (2,576 ticks) in
    about 90 seconds, and `CovWorker.gd _verify_replay` (the same live
    input-application loop, with no legality-report side work)
    reproduces the same stall independently of `_scan_replay`, which
    rules out a bug in `_scan_replay`'s own extra work as the cause. No
    committed game file may change to chase this further (see README.md
    "Known defect" for the full evidence trail). `run_worker`'s own
    per-job timeout (`JOB_TIMEOUT_S`) already kills a job that runs this
    long and raises `WorkerStalled`, the same signal `coordinator.run()`
    already treats as "drop this one node, keep the rest of the search"
    rather than a crash. `scan_roots` applies the same rule here: on a
    stall, it reads whatever `root_tick` records `_scan_replay` already
    wrote to the job's own output file before the kill (a line the kill
    cut off mid-write is simply skipped, not treated as a parse error),
    instead of losing the whole mirror's scan or capping every mirror's
    scan at one hand-picked tick up front, which this tool no longer
    does.
    """
    roots = [{"mode": "match_start", "seed": 1}]
    for i, replay in enumerate(replays):
        name = f"scan{i}"
        job_out = work.jobs / f"{name}.jsonl"
        try:
            out = run_worker(work, name, {"base": {"mode": "replay_scan", "replay": replay}})
        except WorkerStalled as exc:
            print(f"{replay}: {exc}; keeping the root_tick records it wrote before the stall", file=sys.stderr)
            out = job_out
        found = 0
        for line in out.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # a kill mid-write can leave a partial trailing line
            if rec.get("kind") == "root_tick" and found < roots_per_mirror:
                roots.append({"mode": "replay_prefix", "replay": rec["replay"], "up_to_tick": rec["tick"]})
                found += 1
    out_path.write_text(json.dumps(roots, indent=2))
    print(f"wrote {len(roots)} roots to {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    worker = sub.add_parser("worker")
    worker.add_argument("--job", type=Path, required=True)
    worker.add_argument("--name", required=True)
    scan = sub.add_parser("scan-roots")
    scan.add_argument("--replay", action="append", required=True, dest="replays")
    scan.add_argument("--out", type=Path, required=True)
    scan.add_argument("--roots-per-mirror", type=int, default=1, help="how many of a mirror's own genuine decision points become replay_prefix roots")
    coord = sub.add_parser("coordinate")
    coord.add_argument("--out", type=Path, required=True)
    coord.add_argument("--jobs", type=int, default=4)
    coord.add_argument("--roots", type=Path, required=True)
    coord.add_argument("--max-per-player", type=int, default=12, help="caps the discretized candidates tried per player at each decision point (design step 5 groups continuous inputs into classes; this bounds how many of those classes one decision point tries, so a search step stays tractable -- see README.md)")
    corpus_p = sub.add_parser("corpus")
    corpus_p.add_argument("--kept-paths", type=Path, required=True)
    corpus_p.add_argument("--out", type=Path, required=True)
    fidelity_p = sub.add_parser("fidelity")
    fidelity_p.add_argument("--corpus", type=Path, required=True)
    args = parser.parse_args()

    rc_run.start_display()
    work = rc_run.Work(root=args.work.resolve())
    if args.cmd == "prepare":
        prepare(work)
        return 0
    if args.cmd == "worker":
        job = json.loads(args.job.read_text())
        out = run_worker(work, args.name, job)
        print(out)
        return 0
    if args.cmd == "scan-roots":
        scan_roots(work, args.replays, args.out, args.roots_per_mirror)
        return 0
    if args.cmd == "coordinate":
        import coordinator

        readme_path = TOOL_DIR / "README.md"
        return coordinator.run(
            work, args.roots, args.out, args.jobs, run_worker,
            args.max_per_player, readme_path if readme_path.exists() else None,
            REPO,
        )
    if args.cmd == "corpus":
        import corpus

        kept = json.loads(args.kept_paths.read_text())
        corpus.build(work, kept, args.out, run_worker)
        return 0
    if args.cmd == "fidelity":
        import corpus

        manifest = json.loads((args.corpus / "manifest.json").read_text())
        result = corpus.check_fidelity(work, manifest, run_worker)
        (args.corpus / "fidelity_report.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({"pass": result["pass"]}, indent=2))
        return 0 if result["pass"] else 1
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    sys.exit(main())
