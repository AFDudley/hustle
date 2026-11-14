#!/usr/bin/env python3
"""Headless replay check for Your Only Move Is HUSTLE.

See tools/replay_check/README.md for the design. This script runs only inside
the container from Containerfile; start it with container.sh. Summary:

  prepare   Copy the project to a work directory and import the assets.
  generate  Play random matches through the real action menu. Write one
            replay per match and a .match.json file with its inputs.
  check     Play each replay back twice. Compare the two playback traces
            (determinism), the live match (replay fidelity), and an optional
            golden trace (regression). On a mismatch, dump both snapshots at
            the first tick of each divergence and print the variables that
            differ.
  record    Play each replay back once and store the traces as golden.

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import atexit
import concurrent.futures
import dataclasses
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator

# GodotSteam g351-s155-gs3184 editor build. The Containerfile installs it in
# GODOT_HOME and checks its SHA-256.
GODOT_BINARY = "linux-351-editor.64"
# Every Godot run: no window on the Xvfb display, and no audio device.
GODOT_FLAGS = ["--no-window", "--audio-driver", "Dummy"]


def godot_prefix() -> list[str]:
    """Command prefix for a game run: vglrun when container.sh --gpu is set.

    VirtualGL's EGL back end renders Godot's GLX calls on the GPU that the
    container got through CDI, and copies each frame into the Xvfb window.
    """
    if os.environ.get("REPLAY_CHECK_GPU") == "1":
        return ["vglrun", "-d", "egl"]
    return []


TOOL_DIR = Path(__file__).resolve().parent
REPO = TOOL_DIR.parent.parent
SCENE = "res://tools/replay_check/ReplayCheck.tscn"
IMPORT_PRESET = "ReplayCheckImport"
JOB_TIMEOUT_S = 900
MAX_DIFF_LINES = 60
MAX_EXPLAINED_EPISODES = 20

# Godot's export step needs a preset. The pack it writes is discarded; the
# export exists only because it imports every asset and then exits.
EXPORT_PRESETS = f"""[preset.0]

name="{IMPORT_PRESET}"
platform="Linux/X11"
runnable=true
custom_features=""
export_filter="all_resources"
include_filter=""
exclude_filter=""
export_path=""
script_export_mode=1
script_encryption_key=""

[preset.0.options]

binary_format/64_bits=true
binary_format/embed_pck=false
texture_format/s3tc=true
texture_format/etc=false
texture_format/etc2=false
"""


class CheckError(Exception):
    """A step failed. The message says which step and why."""


@dataclasses.dataclass(frozen=True)
class Work:
    """Layout of the work directory. Nothing in it is source.

    `coverage` is the coverage output directory (tools/coverage), or None.
    When it is set, every job writes its probe counts to coverage/hits/.
    """

    root: Path
    coverage: Path | None = None

    @property
    def godot(self) -> Path:
        return Path(os.environ.get("GODOT_HOME", "/opt/godotsteam")) / GODOT_BINARY

    @property
    def project(self) -> Path:
        return self.root / "project"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs"


# --- prepare -----------------------------------------------------------------


def start_display() -> None:
    """Start Xvfb and set DISPLAY. The GodotSteam build is an X11 build.

    Xvfb writes its display number to -displayfd when it accepts
    connections, so the read below returns exactly when the display is
    ready, or at EOF if Xvfb exits.
    """
    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(
        [
            "Xvfb",
            "-displayfd",
            str(write_fd),
            "-screen",
            "0",
            "1280x720x24",
            "-nolisten",
            "tcp",
        ],
        pass_fds=(write_fd,),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(write_fd)
    with os.fdopen(read_fd) as fh:
        number = fh.readline().strip()
    if not number:
        raise CheckError(f"Xvfb exited with {proc.wait()} before it reported a display")
    os.environ["DISPLAY"] = f":{number}"
    atexit.register(proc.terminate)


def copy_project(work: Work) -> None:
    """Mirror the repo into the work copy. .import survives, so reimport is incremental."""
    work.project.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "rsync",
            "-a",
            "--delete",
            "--exclude",
            ".git",
            "--exclude",
            ".scratch",
            "--exclude",
            ".import",
            "--exclude",
            "export_presets.cfg",
            f"{REPO}/",
            f"{work.project}/",
        ],
        check=True,
    )
    (work.project / "export_presets.cfg").write_text(EXPORT_PRESETS)


def audio_shim_env(project: Path) -> dict[str, str]:
    """Environment for a Godot run that paces audio frame by frame.

    Loads audio_sleep_shim.c (built into the image) and gives it the exact
    sleep of the Dummy driver, in nanoseconds. Godot 3.5 computes that sleep
    in AudioDriverDummy::init and ::thread_func:
        buffer_frames = closest_power_of_2(int(output_latency * mix_rate / 1000))
        usec = uint64(float32(buffer_frames / float32(mix_rate)) * 1e6)
    and OS_Unix::delay_usec passes usec * 1000 to nanosleep. Defaults:
    mix_rate 44100, output_latency 15 ms, so 512 frames and 11,609,000 ns.
    The tool must still check its speed: a different sleep means no match.
    """
    text = (project / "project.godot").read_text()
    section = re.search(r"^\[audio\]\n(.*?)(?=^\[|\Z)", text, re.M | re.S)
    settings = (
        dict(re.findall(r"^(\w+)=(\S+)$", section.group(1), re.M)) if section else {}
    )
    mix_rate = int(settings.get("mix_rate", 44100))
    latency = int(settings.get("output_latency", 15))
    x = latency * mix_rate // 1000
    nxt = 1 << (x - 1).bit_length()
    prv = nxt >> 1 if nxt != x else x
    frames = prv if (nxt - x) > (x - prv) else nxt
    f32 = struct.unpack("f", struct.pack("f", frames / mix_rate))[0]
    usec = int(struct.unpack("f", struct.pack("f", f32 * 1000000))[0])
    return {
        "LD_PRELOAD": os.environ["AUDIO_SLEEP_SHIM"],
        "AUDIO_SLEEP_NS": str(usec * 1000),
        "AUDIO_SLEEP_NEW_NS": "1000000",
    }


def import_assets(work: Work) -> None:
    pack = work.root / "import.pck"
    log = work.root / "import.log"
    with log.open("w") as fh:
        result = subprocess.run(
            [
                str(work.godot),
                *GODOT_FLAGS,
                "--path",
                str(work.project),
                "--export-pack",
                IMPORT_PRESET,
                str(pack),
            ],
            stdout=fh,
            stderr=subprocess.STDOUT,
            timeout=JOB_TIMEOUT_S,
            check=False,
        )
    if result.returncode != 0 or not pack.exists():
        raise CheckError(
            f"asset import failed with exit {result.returncode}; see {log}"
        )
    pack.unlink()


def prepare(work: Work) -> None:
    if not work.godot.exists():
        raise CheckError(f"{work.godot} does not exist; run inside the container")
    copy_project(work)
    import_assets(work)
    print(f"prepared {work.project}")


def characters() -> list[str]:
    """Playable characters, read from the uncommented entries of Global.name_paths."""
    text = (REPO / "Global.gd").read_text()
    block = re.search(r"var name_paths = \{(.*?)\n\}", text, re.S)
    if block is None:
        raise CheckError("Global.gd: no name_paths dictionary")
    return re.findall(r'^\s*"(\w+)":', block.group(1), re.M)


# --- jobs --------------------------------------------------------------------


@dataclasses.dataclass
class JobResult:
    name: str
    done: dict[str, Any]
    log: Path


def run_job(work: Work, name: str, job: dict[str, Any]) -> JobResult:
    """Run one ReplayCheck.gd job. Any SCRIPT ERROR fails the job."""
    work.jobs.mkdir(parents=True, exist_ok=True)
    job_file = work.jobs / f"{name}.json"
    job_file.write_text(json.dumps(job))
    log = work.jobs / f"{name}.log"
    # user:// resolves under XDG_DATA_HOME. One directory per job keeps the
    # user's real game data untouched and keeps parallel jobs apart. The
    # directory starts empty on every run: the game writes state there
    # (modded.json, playerdata.json) that changes the next launch. For
    # example, ModLoader's version-transition gate turns mods off on the
    # second launch, and with mods off the fighters get no Hooks node.
    xdg = work.jobs / f"{name}.xdg"
    if xdg.exists():
        shutil.rmtree(xdg)
    env = dict(os.environ, XDG_DATA_HOME=str(xdg))
    if work.coverage is not None:
        env["COVERAGE_OUT"] = str(work.coverage / "hits" / f"{name}.json")
    with log.open("w") as fh:
        result = subprocess.run(
            [
                *godot_prefix(),
                str(work.godot),
                *GODOT_FLAGS,
                "--path",
                str(work.project),
                "--fixed-fps",
                "60",
                SCENE,
                f"--rc-job={job_file}",
            ],
            stdout=fh,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=JOB_TIMEOUT_S,
            check=False,
        )
    text = log.read_text(errors="replace")
    errors = script_errors(text)
    if errors:
        raise CheckError(
            f"{name}: {len(errors)} script errors; first: {errors[0]}; log {log}"
        )
    done = re.search(r"^REPLAY_CHECK_DONE (.*)$", text, re.M)
    if result.returncode != 0 or done is None:
        fail = re.search(r"^REPLAY_CHECK_FAIL: (.*)$", text, re.M)
        reason = (
            fail.group(1) if fail else f"exit {result.returncode}, no REPLAY_CHECK_DONE"
        )
        raise CheckError(f"{name}: {reason}; log {log}")
    return JobResult(name, json.loads(done.group(1)), log)


def script_errors(text: str) -> list[str]:
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if line.startswith("SCRIPT ERROR:"):
            where = lines[i + 1].strip() if i + 1 < len(lines) else ""
            out.append(f"{line} {where}")
    return out


def ignore_list() -> list[str]:
    names = []
    for raw in (TOOL_DIR / "ignore.txt").read_text().splitlines():
        name = raw.split("#", 1)[0].strip()
        if name:
            names.append(name)
    return names


def playback_job(
    replay: Path, trace: Path, dump_tick: int | None = None, dump: Path | None = None
) -> dict[str, Any]:
    job: dict[str, Any] = {
        "mode": "playback",
        "replay": str(replay),
        "trace": str(trace),
        "ignore": ignore_list(),
    }
    if dump_tick is not None and dump is not None:
        job["dump_ticks"] = [dump_tick]
        job["dump"] = str(dump)
    return job


def live_job(
    replay: Path, trace: Path, dump_tick: int | None = None, dump: Path | None = None
) -> dict[str, Any]:
    """Play a replay's recorded inputs through the live path (see
    ReplayCheck.gd, mode `live`). The lab book's decision 2 makes the live
    path the one the model follows."""
    job = playback_job(replay, trace, dump_tick, dump)
    job["mode"] = "live"
    return job


def generate_job(
    p1: str,
    p2: str,
    seed: int,
    replay: Path,
    trace: Path,
    dump_tick: int | None = None,
    dump: Path | None = None,
) -> dict[str, Any]:
    job: dict[str, Any] = {
        "mode": "generate",
        "p1": p1,
        "p2": p2,
        "seed": seed,
        "replay_out": str(replay),
        "trace": str(trace),
        "ignore": ignore_list(),
        "reverse_chance": 0.3,
        "feint_chance": 0.2,
        "extra_toggle_chance": 0.2,
    }
    if dump_tick is not None and dump is not None:
        job["dump_ticks"] = [dump_tick]
        job["dump"] = str(dump)
    return job


# --- traces ------------------------------------------------------------------


def read_trace(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


@dataclasses.dataclass
class Episode:
    """A run of consecutive ticks where two traces differ."""

    tick: int
    ticks: int
    parts: list[str]


@dataclasses.dataclass
class Comparison:
    episodes: list[Episode]
    differing_ticks: int
    final_equal: bool
    problem: str | None  # tick sequence or length mismatch

    @property
    def ok(self) -> bool:
        return not self.episodes and self.problem is None

    def summary(self) -> str:
        parts = []
        if self.problem:
            parts.append(self.problem)
        if self.episodes:
            parts.append(
                f"{len(self.episodes)} divergence episodes, "
                f"{self.differing_ticks} differing ticks, "
                f"final state {'equal' if self.final_equal else 'DIFFERENT'}"
            )
        return "; ".join(parts)


def compare_traces(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> Comparison:
    """Find every episode where the traces differ.

    A difference does not stop the comparison: the simulation can converge
    again, and a later, independent difference stays visible.
    """
    episodes: list[Episode] = []
    differing = 0
    problem = None
    current: Episode | None = None
    for ra, rb in zip(a, b):
        if ra["t"] != rb["t"]:
            problem = f"tick sequence differs at {ra['t']} against {rb['t']}"
            break
        if ra["h"] == rb["h"]:
            current = None
            continue
        differing += 1
        parts = ["game"] if ra["g"] != rb["g"] else []
        for name in sorted(set(ra["o"]) | set(rb["o"])):
            if ra["o"].get(name) != rb["o"].get(name):
                parts.append(name)
        if current is None:
            current = Episode(ra["t"], 0, [])
            episodes.append(current)
        current.ticks += 1
        current.parts = sorted(set(current.parts) | set(parts))
    if problem is None and len(a) != len(b):
        problem = f"trace lengths differ: {len(a)} against {len(b)} ticks"
    final_equal = bool(a and b and a[-1]["h"] == b[-1]["h"] and len(a) == len(b))
    return Comparison(episodes, differing, final_equal, problem)


def flatten(value: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key in sorted(value):
            yield from flatten(value[key], f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from flatten(item, f"{prefix}[{i}]")
    else:
        yield prefix, value


def read_dumps(path: Path) -> dict[int, Any]:
    out = {}
    for line in path.read_text().splitlines():
        if line:
            record = json.loads(line)
            out[record["t"]] = record["s"]
    return out


def snapshot_diff(snap_a: Any, snap_b: Any) -> list[str]:
    flat_a = dict(flatten(snap_a))
    flat_b = dict(flatten(snap_b))
    lines = []
    for key in sorted(set(flat_a) | set(flat_b)):
        # Compare the JSON text, so 1 and 1.0 count as different.
        va = json.dumps(flat_a.get(key, "<absent>"))
        vb = json.dumps(flat_b.get(key, "<absent>"))
        if va != vb:
            lines.append(f"      {key}: {va} -> {vb}")
    return lines


# --- commands ----------------------------------------------------------------


def parse_seeds(spec: str) -> list[int]:
    seeds: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            seeds.extend(range(int(lo), int(hi) + 1))
        else:
            seeds.append(int(part))
    return seeds


def parse_pairs(spec: str) -> list[tuple[str, str]]:
    names = characters()
    if spec == "all":
        return [(a, b) for a in names for b in names]
    pairs = []
    for part in spec.split(","):
        p1, p2 = part.split(":")
        for name in (p1, p2):
            if name not in names:
                raise CheckError(f"unknown character {name}; known: {', '.join(names)}")
        pairs.append((p1, p2))
    return pairs


def match_name(p1: str, p2: str, seed: int) -> str:
    return f"{p1}_{p2}_{seed}"


def cmd_generate(
    work: Work, out: Path, pairs: list[tuple[str, str]], seeds: list[int], jobs: int
) -> int:
    out.mkdir(parents=True, exist_ok=True)
    tasks = [(p1, p2, seed) for p1, p2 in pairs for seed in seeds]

    def one(task: tuple[str, str, int]) -> str:
        p1, p2, seed = task
        name = match_name(p1, p2, seed)
        res = run_job(
            work,
            f"gen_{name}",
            generate_job(
                p1, p2, seed, out / f"{name}.replay", work.jobs / f"gen_{name}.trace"
            ),
        )
        # The sidecar lets `check` replay the live match for the fidelity check.
        (out / f"{name}.match.json").write_text(
            json.dumps({"p1": p1, "p2": p2, "seed": seed})
        )
        d = res.done
        return (
            f"{name}: {d['turns']} turns, {d['ticks_traced']} ticks, "
            f"finished={d['game_finished']}, hp {d['p1_hp']}/{d['p2_hp']}"
        )

    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(one, t): t for t in tasks}
        for future in concurrent.futures.as_completed(futures):
            try:
                print(future.result(), flush=True)
            except CheckError as exc:
                failures += 1
                print(f"FAIL {exc}", flush=True)
    print(f"generated {len(tasks) - failures}/{len(tasks)} matches in {out}")
    return 1 if failures else 0


def explain(
    work: Work,
    name: str,
    ticks: list[int],
    side_a: tuple[str, dict[str, Any]],
    side_b: tuple[str, dict[str, Any]],
) -> dict[int, list[str]]:
    """Rerun both sides with dumps at `ticks`. Return the differing variables per tick."""
    dumps = []
    for label, job in (side_a, side_b):
        stem = f"dump_{name}_{label}"
        dump = work.jobs / f"{stem}.jsonl"
        job = dict(
            job,
            dump_ticks=ticks,
            dump=str(dump),
            trace=str(work.jobs / f"{stem}.trace"),
        )
        if job["mode"] == "generate":
            job["replay_out"] = str(work.jobs / f"{stem}.replay")
        run_job(work, stem, job)
        dumps.append(read_dumps(dump))
    out = {}
    for tick in ticks:
        if tick not in dumps[0] or tick not in dumps[1]:
            out[tick] = [f"      no snapshot at tick {tick}"]
            continue
        lines = snapshot_diff(dumps[0][tick], dumps[1][tick])
        if len(lines) > MAX_DIFF_LINES:
            lines = lines[:MAX_DIFF_LINES] + [
                f"      ... {len(lines) - MAX_DIFF_LINES} more"
            ]
        out[tick] = lines
    return out


def cmd_check(work: Work, replays: Path, golden: Path | None, jobs: int) -> int:
    files = sorted(replays.glob("*.replay"))
    if not files:
        raise CheckError(f"no .replay files in {replays}")

    def one(replay: Path) -> tuple[bool, list[str]]:
        name = replay.stem
        report = []
        traces = []
        pb_job = playback_job(replay, work.jobs / f"pb_{name}_a.trace")
        for run in ("a", "b"):
            trace = work.jobs / f"pb_{name}_{run}.trace"
            run_job(work, f"pb_{name}_{run}", playback_job(replay, trace))
            traces.append(read_trace(trace))
        ok = True
        comparisons: list[
            tuple[str, list[dict[str, Any]], tuple[str, dict[str, Any]]]
        ] = []
        comparisons.append(
            ("determinism (playback run 2)", traces[1], ("playback2", pb_job))
        )
        # Every replay, generated or human: its own inputs on the live path.
        live_trace = work.jobs / f"live_{name}.trace"
        lv_job = live_job(replay, live_trace)
        run_job(work, f"live_{name}", lv_job)
        comparisons.append(
            ("live path (recorded inputs)", read_trace(live_trace), ("live_inputs", lv_job))
        )
        sidecar = replays / f"{name}.match.json"
        if sidecar.exists():
            # Play the live match again with the current code and ignore list.
            # The new replay must equal the stored one byte for byte, or the
            # fidelity comparison would compare two different matches.
            spec = json.loads(sidecar.read_text())
            regen_replay = work.jobs / f"regen_{name}.replay"
            regen_trace = work.jobs / f"regen_{name}.trace"
            gen = generate_job(
                spec["p1"], spec["p2"], spec["seed"], regen_replay, regen_trace
            )
            run_job(work, f"regen_{name}", gen)
            if regen_replay.read_bytes() != replay.read_bytes():
                ok = False
                report.append(
                    f"  FAIL  live match: replaying the generator gave a different "
                    f"replay ({regen_replay}); the generator or the game changed"
                )
            else:
                comparisons.append(
                    (
                        "replay fidelity (live match)",
                        read_trace(regen_trace),
                        ("live", gen),
                    )
                )
        if golden is not None:
            gold = golden / f"{name}.trace"
            if not gold.exists():
                raise CheckError(f"no golden trace {gold}")
            comparisons.append(
                ("regression (golden)", read_trace(gold), ("golden", {}))
            )
        for label, other, (other_label, other_job) in comparisons:
            comparison = compare_traces(traces[0], other)
            if comparison.ok:
                report.append(f"  ok    {label}")
                continue
            ok = False
            report.append(f"  FAIL  {label}: {comparison.summary()}")
            shown = comparison.episodes[:MAX_EXPLAINED_EPISODES]
            details: dict[int, list[str]] = {}
            if other_job and shown:
                details = explain(
                    work,
                    f"{name}_{other_label}",
                    [e.tick for e in shown],
                    ("playback", pb_job),
                    (other_label, other_job),
                )
            for episode in shown:
                report.append(
                    f"    tick {episode.tick} (+{episode.ticks} ticks) in "
                    + ", ".join(episode.parts)
                )
                report.extend(details.get(episode.tick, []))
            hidden = len(comparison.episodes) - len(shown)
            if hidden:
                report.append(f"    ... {hidden} more episodes")
            if not other_job:
                report.append(
                    "    (golden traces hold hashes only; dump the golden source to see variables)"
                )
        return ok, report

    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(one, f): f for f in files}
        for future in concurrent.futures.as_completed(futures):
            replay = futures[future]
            try:
                ok, report = future.result()
            except CheckError as exc:
                ok, report = False, [f"  ERROR {exc}"]
            failures += 0 if ok else 1
            print(f"{'PASS' if ok else 'FAIL'} {replay.name}", flush=True)
            print("\n".join(report), flush=True)
    print(f"{len(files) - failures}/{len(files)} replays pass")
    return 1 if failures else 0


def cmd_record(work: Work, replays: Path, golden: Path, jobs: int) -> int:
    files = sorted(replays.glob("*.replay"))
    if not files:
        raise CheckError(f"no .replay files in {replays}")
    golden.mkdir(parents=True, exist_ok=True)

    def one(replay: Path) -> str:
        trace = golden / f"{replay.stem}.trace"
        res = run_job(work, f"rec_{replay.stem}", playback_job(replay, trace))
        return f"{replay.stem}: {res.done['ticks_traced']} ticks"

    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(one, f): f for f in files}
        for future in concurrent.futures.as_completed(futures):
            try:
                print(future.result(), flush=True)
            except CheckError as exc:
                failures += 1
                # A partial trace must not become a golden file.
                (golden / f"{futures[future].stem}.trace").unlink(missing_ok=True)
                print(f"FAIL {exc}", flush=True)
    print(f"recorded {len(files) - failures}/{len(files)} golden traces in {golden}")
    return 1 if failures else 0


def cmd_census(work: Work, replays: Path, out: Path, jobs: int) -> int:
    """Play each replay on the live path and write one census line per decision
    (ReplayCheck.gd `_write_census`): each player's state, busy flag, legal
    actions (Legality.gd) and recorded input. Then count how many recorded
    inputs the legality rule accepts."""
    files = sorted(replays.glob("*.replay"))
    if not files:
        raise CheckError(f"no .replay files in {replays}")
    out.mkdir(parents=True, exist_ok=True)

    def one(replay: Path) -> Path:
        census = out / f"{replay.stem}.jsonl"
        job = live_job(replay, work.jobs / f"census_{replay.stem}.trace")
        job["census"] = str(census)
        run_job(work, f"census_{replay.stem}", job)
        return census

    total = legal = 0
    misses: list[str] = []
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(one, f): f for f in files}
        for future in concurrent.futures.as_completed(futures):
            try:
                census = future.result()
            except CheckError as exc:
                failures += 1
                print(f"FAIL {futures[future].name}: {exc}", flush=True)
                continue
            for raw in census.read_text().splitlines():
                row = json.loads(raw)
                for pid in ("p1", "p2"):
                    p = row[pid]
                    if p["input"] is None:
                        continue
                    total += 1
                    names = [a.rstrip("*") for a in p["legal"]]
                    if p["input"]["action"] in names:
                        legal += 1
                    else:
                        misses.append(
                            f"{census.stem} tick {row['t']} {pid}: {p['state']} -> "
                            f"{p['input']['action']} (busy={p['busy']}, feinting={p['feinting']})"
                        )
    print(f"census: {legal}/{total} recorded inputs are in the legal list")
    for miss in misses:
        print(f"  not legal: {miss}")
    print(f"{len(files) - failures}/{len(files)} replays ran; census files in {out}")
    return 1 if failures else 0


def load_tool(directory: str, module: str) -> Any:
    """Import tools/<directory>/<module>.py. tools/ is not a Python package."""
    path = TOOL_DIR.parent / directory / f"{module}.py"
    spec = importlib.util.spec_from_file_location(f"{directory}_{module}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def start_coverage(work: Work) -> None:
    """Instrument the work copy (tools/coverage) and start an empty hits directory."""
    assert work.coverage is not None
    cov_map = load_tool("coverage", "instrument").instrument(work.project)
    work.coverage.mkdir(parents=True, exist_ok=True)
    (work.coverage / "map.json").write_text(json.dumps(cov_map))
    hits = work.coverage / "hits"
    if hits.exists():
        shutil.rmtree(hits)
    hits.mkdir()
    print(f"coverage: {cov_map['size']} probes in {len(cov_map['files'])} files")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--work",
        type=Path,
        required=True,
        help="work directory (project copy, job logs); container.sh sets it",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=max(1, (os.cpu_count() or 2) // 2),
        help="parallel Godot processes",
    )
    parser.add_argument(
        "--coverage",
        type=Path,
        help="measure GDScript line coverage; write map, hits and reports here",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    gen = sub.add_parser("generate")
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument(
        "--pairs",
        default="all",
        help='"all" or P1:P2,P1:P2 (names from Global.name_paths)',
    )
    gen.add_argument("--seeds", default="1", help="e.g. 1-5 or 1,7,9")
    chk = sub.add_parser("check")
    chk.add_argument("--replays", type=Path, required=True)
    chk.add_argument("--golden", type=Path)
    rec = sub.add_parser("record")
    rec.add_argument("--replays", type=Path, required=True)
    rec.add_argument("--golden", type=Path, required=True)
    cen = sub.add_parser("census")
    cen.add_argument("--replays", type=Path, required=True)
    cen.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if os.environ.get("REPLAY_CHECK_CONTAINER") != "1":
        print(
            "error: run.py runs only inside its container; use tools/replay_check/container.sh",
            file=sys.stderr,
        )
        return 2
    work = Work(args.work.resolve(), args.coverage.resolve() if args.coverage else None)
    try:
        start_display()
        # Every command syncs the work copy first, so it never runs stale source.
        prepare(work)
        if work.coverage is not None:
            start_coverage(work)
        if args.cmd == "prepare":
            return 0
        status = run_command(args, work)
        if work.coverage is not None:
            print(load_tool("coverage", "report").write_reports(work.coverage), end="")
        return status
    except CheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def run_command(args: argparse.Namespace, work: Work) -> int:
    if args.cmd == "generate":
        return cmd_generate(
            work,
            args.out.resolve(),
            parse_pairs(args.pairs),
            parse_seeds(args.seeds),
            args.jobs,
        )
    if args.cmd == "check":
        return cmd_check(
            work,
            args.replays.resolve(),
            args.golden.resolve() if args.golden else None,
            args.jobs,
        )
    if args.cmd == "record":
        return cmd_record(
            work, args.replays.resolve(), args.golden.resolve(), args.jobs
        )
    if args.cmd == "census":
        return cmd_census(work, args.replays.resolve(), args.out.resolve(), args.jobs)
    raise AssertionError(args.cmd)


if __name__ == "__main__":
    sys.exit(main())
