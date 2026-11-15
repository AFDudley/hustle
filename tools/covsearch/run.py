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


def prepare(work: rc_run.Work) -> dict:
    if not work.godot.exists():
        raise rc_run.CheckError(f"{work.godot} does not exist; run inside the container")
    rc_run.copy_project(work)
    cov_map = cs_instrument.instrument(work.project)
    cs_instrument.write_map(cov_map, work.root / "probe_map.json")
    rc_run.import_assets(work)
    print(f"prepared {work.project}: {cov_map['size']} probes over {len(cov_map['files'])} files")
    return cov_map


def run_worker(work: rc_run.Work, name: str, job: dict) -> Path:
    """Run one CovWorker.gd job. Returns the path to its JSONL output."""
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
            timeout=rc_run.JOB_TIMEOUT_S,
            check=False,
        )
    text = log.read_text()
    if "SCRIPT ERROR" in text or "COVSEARCH_FAIL" in text:
        raise rc_run.CheckError(f"worker job {name} failed; see {log}")
    if result.returncode != 0:
        raise rc_run.CheckError(f"worker job {name} exited {result.returncode}; see {log}")
    return job_out


def scan_roots(work: rc_run.Work, replays: list[str], out_path: Path) -> None:
    """Run replay_scan for each mirror, then write roots.json.

    Design step 3: the match-start root, plus every decision point of each
    mirror, found by replaying it on the live path (CovWorker.gd
    _scan_replay). Each replay_scan run also writes its own branch and
    legality records; scan-roots does not fold them into a coverage total
    (the coordinator re-expands each root), so no coverage is double
    counted and no coverage silently depends on this command having run.
    """
    roots = [{"mode": "match_start", "seed": 1}]
    for i, replay in enumerate(replays):
        out = run_worker(work, f"scan{i}", {"base": {"mode": "replay_scan", "replay": replay}})
        for line in out.read_text().splitlines():
            rec = json.loads(line)
            if rec["kind"] == "root_tick":
                roots.append({"mode": "replay_prefix", "replay": rec["replay"], "up_to_tick": rec["tick"]})
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
    coord = sub.add_parser("coordinate")
    coord.add_argument("--out", type=Path, required=True)
    coord.add_argument("--jobs", type=int, default=4)
    coord.add_argument("--roots", type=Path, required=True)
    coord.add_argument("--max-per-player", type=int, default=12, help="caps the discretized candidates tried per player at each decision point (design step 5 groups continuous inputs into classes; this bounds how many of those classes one decision point tries, so a search step stays tractable -- see README.md)")
    coord.add_argument("--time-budget", type=float, default=0.0, help="stop after this many seconds (0 = until the frontier empties or every probe is hit; the design forbids an iteration cap, so this is a wall-clock escape hatch for interactive runs only)")
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
        scan_roots(work, args.replays, args.out)
        return 0
    if args.cmd == "coordinate":
        import coordinator

        return coordinator.run(work, args.roots, args.out, args.jobs, args.time_budget, run_worker, args.max_per_player)
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
