#!/usr/bin/env python3
"""Headless UI test for Your Only Move Is HUSTLE.

See tools/ui_test/README.md for the design. This script runs only inside
the tools/replay_check container; start it with
tools/replay_check/container.sh --entrypoint python3, or a thin wrapper
that does the same (see README.md). It reuses tools/replay_check/run.py's
runtime helpers (Work, prepare, start_display, godot_prefix,
script_errors, characters, parse_pairs, parse_seeds) rather than copying
them. It does not import tools/replay_check/run.py's argparse setup: its
own CLI is separate.

generate  Boot the real game (project.godot's own main_scene, Main.tscn),
          drive it through the main menu, character select, one full
          singleplayer match, and the post-match pause/save/exit UI, all
          with synthetic input events routed through
          Input.parse_input_event -- the same entry point real OS input
          uses. See tools/ui_test/UIPlay.gd for the driver and
          tools/ui_test/README.md for why this differs from
          tools/replay_check's direct function calls.

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

TOOL_DIR = Path(__file__).resolve().parent
REPO = TOOL_DIR.parent.parent
JOB_TIMEOUT_S = 600

AUTOLOAD_NAME = "UIPlay"
DRIVER_AUTOLOAD = "res://tools/ui_test/UIPlay.tscn"


def _load_tool(directory: str, module: str) -> Any:
    """Import tools/<directory>/<module>.py. tools/ is not a Python package.

    Mirrors tools/replay_check/run.py's own load_tool: tools/ is a plain
    directory, not a package, so a normal `import` cannot find a sibling
    tool's module. Each caller loads what it needs this way instead.
    """
    path = REPO / "tools" / directory / f"{module}.py"
    spec = importlib.util.spec_from_file_location(f"{directory}_{module}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rc = _load_tool("replay_check", "run")


class CheckError(Exception):
    """A step failed. The message says which step and why."""


def register_autoload(work) -> None:
    """Register tools/ui_test/UIPlay.tscn as an autoload in the work copy.

    Inserted the same way tools/coverage/instrument.py adds __COV__: right
    after the [autoload] header, so it exists alongside Global, Network
    and the rest. It must be an autoload, not the scene passed on Godot's
    command line, so it survives Global.reload() (SceneTree
    reload_current_scene()) when the driven match quits to the main menu;
    see UIPlay.gd's header comment for why.
    """
    project_file = work.project / "project.godot"
    text = project_file.read_text()
    if "\n[autoload]\n" not in text:
        raise CheckError(f"{project_file}: no [autoload] section")
    entry = f'{AUTOLOAD_NAME}="*{DRIVER_AUTOLOAD}"'
    project_file.write_text(
        text.replace("\n[autoload]\n", f"\n[autoload]\n\n{entry}\n", 1)
    )


@dataclasses.dataclass
class JobResult:
    name: str
    done: dict[str, Any]
    milestones: list[dict[str, Any]]
    log: Path


def run_job(work, name: str, job: dict[str, Any]) -> JobResult:
    """Run one UIPlay.gd job. Any SCRIPT ERROR or UI_TEST_FAIL fails it.

    Boots Godot with no scene argument, so it loads project.godot's own
    run/main_scene (Main.tscn) -- the real game boot path -- with the
    UIPlay autoload already registered by register_autoload(). Mirrors
    replay_check's run_job: a fresh XDG_DATA_HOME per job, since
    ModLoader.gd turns mods off on a user directory's second launch (the
    version-transition gate), and with mods off BaseObj gets no Hooks node.
    """
    work.jobs.mkdir(parents=True, exist_ok=True)
    job_file = work.jobs / f"{name}.json"
    milestones_path = work.jobs / f"{name}.milestones.jsonl"
    job = dict(job, milestones=str(milestones_path))
    job_file.write_text(json.dumps(job))
    log = work.jobs / f"{name}.log"
    xdg = work.jobs / f"{name}.xdg"
    if xdg.exists():
        shutil.rmtree(xdg)
    env = dict(os.environ, XDG_DATA_HOME=str(xdg))
    if work.coverage is not None:
        env["COVERAGE_OUT"] = str(work.coverage / "hits" / f"{name}.json")
    with log.open("w") as fh:
        result = subprocess.run(
            [
                *rc.godot_prefix(),
                str(work.godot),
                *rc.GODOT_FLAGS,
                "--path",
                str(work.project),
                "--fixed-fps",
                "60",
                f"--ui-job={job_file}",
            ],
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=JOB_TIMEOUT_S,
            check=False,
        )
    text = log.read_text(errors="replace")
    errors = rc.script_errors(text)
    if errors:
        raise CheckError(
            f"{name}: {len(errors)} script errors; first: {errors[0]}; log {log}"
        )
    done = re.search(r"^UI_TEST_DONE (.*)$", text, re.M)
    if result.returncode != 0 or done is None:
        fail = re.search(r"^UI_TEST_FAIL: (.*)$", text, re.M)
        reason = fail.group(1) if fail else f"exit {result.returncode}, no UI_TEST_DONE"
        raise CheckError(f"{name}: {reason}; log {log}")
    milestones = []
    if milestones_path.exists():
        milestones = [
            json.loads(line)
            for line in milestones_path.read_text().splitlines()
            if line
        ]
    return JobResult(name, json.loads(done.group(1)), milestones, log)


def match_name(p1: str, p2: str, seed: int) -> str:
    return f"{p1}_{p2}_{seed}"


def start_coverage(work) -> None:
    """Instrument the work copy (tools/coverage) and start an empty hits dir."""
    assert work.coverage is not None
    cov_map = _load_tool("coverage", "instrument").instrument(work.project)
    work.coverage.mkdir(parents=True, exist_ok=True)
    (work.coverage / "map.json").write_text(json.dumps(cov_map))
    hits = work.coverage / "hits"
    if hits.exists():
        shutil.rmtree(hits)
    hits.mkdir()
    print(f"coverage: {cov_map['size']} probes in {len(cov_map['files'])} files")


def cmd_generate(
    work, out: Path, pairs: list[tuple[str, str]], seeds: list[int], jobs: int
) -> int:
    out.mkdir(parents=True, exist_ok=True)
    tasks = [(p1, p2, seed) for p1, p2 in pairs for seed in seeds]

    def one(task: tuple[str, str, int]) -> tuple[bool, str]:
        p1, p2, seed = task
        name = match_name(p1, p2, seed)
        try:
            res = run_job(work, f"ui_{name}", {"p1": p1, "p2": p2, "seed": seed})
        except CheckError as exc:
            return False, f"FAIL {name}: {exc}"
        (out / f"{name}.milestones.json").write_text(
            json.dumps(res.milestones, indent=2)
        )
        lines = [f"{name}: {res.done.get('turns')} turns, {res.done.get('milestones')} milestones"]
        for m in res.milestones:
            lines.append(f"  {m['t_msec']:>7} ms  {m['milestone']}")
        return True, "\n".join(lines)

    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(one, t): t for t in tasks}
        for future in concurrent.futures.as_completed(futures):
            ok, text = future.result()
            failures += 0 if ok else 1
            print(text, flush=True)
    print(f"{len(tasks) - failures}/{len(tasks)} UI matches reached UI_TEST_DONE in {out}")
    return 1 if failures else 0


def parse_seeds(spec: str) -> list[int]:
    return rc.parse_seeds(spec)


def parse_pairs(spec: str) -> list[tuple[str, str]]:
    return rc.parse_pairs(spec)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument(
        "--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2)
    )
    parser.add_argument(
        "--coverage",
        type=Path,
        help="measure GDScript line coverage; write map, hits and reports here",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument(
        "--pairs",
        default="all",
        help='"all" or P1:P2,P1:P2 (names from Global.name_paths)',
    )
    gen.add_argument("--seeds", default="1", help="e.g. 1-5 or 1,7,9")
    args = parser.parse_args()

    if os.environ.get("REPLAY_CHECK_CONTAINER") != "1":
        print(
            "error: run.py runs only inside its container; see tools/ui_test/README.md",
            file=sys.stderr,
        )
        return 2
    work = rc.Work(args.work.resolve(), args.coverage.resolve() if args.coverage else None)
    try:
        rc.start_display()
        rc.prepare(work)
        register_autoload(work)
        if work.coverage is not None:
            start_coverage(work)
        status = cmd_generate(
            work, args.out.resolve(), parse_pairs(args.pairs), parse_seeds(args.seeds), args.jobs
        )
        if work.coverage is not None:
            print(_load_tool("coverage", "report").write_reports(work.coverage), end="")
        return status
    except (rc.CheckError, CheckError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
