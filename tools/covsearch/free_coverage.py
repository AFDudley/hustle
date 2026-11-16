"""tools/covsearch free-coverage: how much of the Ninja vs Ninja simulation
random play covers, under the operator's TWIN RULE. See tools/covsearch/
README.md and tools/covsearch/free_coverage.md.

One random match:
  1. generate (tools/replay_check "generate" mode, tournament settings,
     Ninja vs Ninja, a seeded RNG) on the PLAIN, uninstrumented project:
     writes one `.replay` file. The random inputs never see instrumented
     code.
  2. live (tools/replay_check "live" mode: the same recorded inputs
     applied through Fighter.on_action_selected) on the PLAIN project:
     one per-tick trace.
  3. live, same replay, on the INSTRUMENTED project (tools/covsearch's
     three-probe-kind instrument.py). `run_job`'s own `work.coverage`
     mechanism sets COVERAGE_OUT, so Probe.gd writes the hit counts at
     exit. One more per-tick trace.
  4. TWIN RULE (operator decision, hard): `compare_traces` (tools/
     replay_check/run.py), with ignore.txt, on the two traces from steps
     2 and 3. `Comparison.ok` (no divergence episode, no tick-sequence or
     length problem) credits this match's probe hits. A twin mismatch
     credits nothing and is recorded with its first episode's tick and
     the fields (`parts`) that first differed.

The 5 Ninja mirrors skip step 1: each already has a `.replay` file (its
own recorded match settings), so only steps 2-4 run for them.

This module only runs inside tools/replay_check's container (same image,
same `Work`/`run_job`/`generate_job`/`live_job`/`compare_traces`): it is
imported by tools/covsearch/run.py, which has already put tools/
replay_check on sys.path under the module name "run" before importing
this file, so `import run as rc_run` below resolves to it.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from pathlib import Path
from typing import Any, Callable

import run as rc_run  # tools/replay_check/run.py

# Tournament settings (lab book decision 3), the same four keys
# CovWorker.gd's TOURNAMENT_OVERRIDE applies, that differ from the
# default lobby and reach the simulation.
TOURNAMENT_OVERRIDE = {
    "stage_width": 600,
    "game_length": 3000,
    "ceiling_height": 500,
    "starting_meter": "1.5",
}

# The 5 Ninja-vs-Ninja ("mirror") replays found under the main checkout's
# .scratch/coverage_baseline/replays (mounted read-only by container.sh
# alongside this worktree -- see container.sh's own "baseline" comment).
# Found by reading each candidate's selected-character strings directly
# (no committed game file changed to find them): 2 are this tool's own
# earlier tournament-settings generations (Ninja_Ninja_1/2, seeds 1 and
# 2); 3 are real human replays from the imported corpus (REPORT.md's set
# B, `/home/rix/code/git_puller/repos/dancing_machine/hustle-replays/
# files/`) whose two players both picked Ninja. `replays/AB` holds all 5.
MIRROR_DIR = Path(
    "/home/rix/code/git_puller/repos/hustle/.scratch/coverage_baseline/replays/AB"
)
_HUMAN = Path("/home/rix/code/git_puller/repos/hustle/.scratch/coverage_baseline/replays/B")
# Mirrors per character: this tool's own two earlier tournament-settings
# generations (<Char>_<Char>_1/2, in replays/AB) plus every human
# 1.10.0-steam mirror of that character in set B (found through
# .scratch/settings/settings.jsonl, "chars" equal to [Char, Char]).
MIRRORS_BY_CHARACTER = {
    "Ninja": [
        MIRROR_DIR / "Ninja_Ninja_1.replay",
        MIRROR_DIR / "Ninja_Ninja_2.replay",
        MIRROR_DIR / "1533592845041143808_2026-8-3-9-43-19.replay",
        MIRROR_DIR / "1535523695794323456_2026-8-8-17-38-26.replay",
        MIRROR_DIR / "1554714058161389618_DokuZyN_v_alex_2026-9-30-0-34-10.replay",
    ],
    "Cowboy": [
        MIRROR_DIR / "Cowboy_Cowboy_1.replay",
        MIRROR_DIR / "Cowboy_Cowboy_2.replay",
        _HUMAN / "1521009085795799190_Redmushroom_v_inf1n1teun1ver3e_2026-6-28-22-52-27.replay",
        _HUMAN / "1523738059403690277_Dude_I_was_STYLING_let_me_DO_IT.replay",
        _HUMAN / "1525175551705223208_GVB20off20VLA20DC20by20hitting20with20bullet20only20120frame20before20the20hitbox20refreh20on202nd20frame20after20bounce.replay",
        _HUMAN / "1526196370514509905_ELFHOOT20TIMEHIFTKIP.replay",
    ],
    "Wizard": [
        MIRROR_DIR / "Wizard_Wizard_1.replay",
        MIRROR_DIR / "Wizard_Wizard_2.replay",
        _HUMAN / "1523900833585172560_insanewizardfight.replay",
        _HUMAN / "1556322703336673381_Wizzard_Finger.replay",
    ],
    "Robot": [
        MIRROR_DIR / "Robot_Robot_1.replay",
        MIRROR_DIR / "Robot_Robot_2.replay",
    ],
    "Mutant": [
        MIRROR_DIR / "Mutant_Mutant_1.replay",
        MIRROR_DIR / "Mutant_Mutant_2.replay",
        _HUMAN / "1532129511037210886_brain_mash.replay",
        _HUMAN / "1536213834564378754_mutant_.replay",
        _HUMAN / "1553938256322633758_DokuZyN_v_Harry__Co_2026-9-27-20-26-15.replay",
    ],
}
MIRROR_REPLAYS = MIRRORS_BY_CHARACTER["Ninja"]


def prepare_plain(work: rc_run.Work) -> None:
    rc_run.prepare(work)


def _episode_record(ep) -> dict:
    return {"tick": ep.tick, "ticks": ep.ticks, "parts": ep.parts}


def process_match(
    work_plain: rc_run.Work,
    work_instr: rc_run.Work,
    name: str,
    replay_path: Path,
    seed: int | None,
    generate_fn: Callable[[], Path] | None,
) -> dict[str, Any]:
    """Runs steps 1 (optional)-4 for one match. Returns one result record."""
    t0 = time.monotonic()
    error = None
    try:
        if generate_fn is not None:
            replay_path = generate_fn()
        trace_plain = work_plain.jobs / f"{name}.plain.trace"
        trace_instr = work_instr.jobs / f"{name}.instr.trace"
        res_plain = rc_run.run_job(work_plain, f"{name}_plain", rc_run.live_job(replay_path, trace_plain))
        res_instr = rc_run.run_job(work_instr, f"{name}_instr", rc_run.live_job(replay_path, trace_instr))
        a = rc_run.read_trace(trace_plain)
        b = rc_run.read_trace(trace_instr)
        cmp = rc_run.compare_traces(a, b)
        hits_path = work_instr.coverage / "hits" / f"{name}_instr.json"
        probes_hit: list[int] = []
        if hits_path.exists():
            probes_hit = [pid for pid, _count in json.loads(hits_path.read_text())]
        twin_pass = cmp.ok
        return {
            "name": name,
            "seed": seed,
            "replay": str(replay_path),
            "twin_pass": twin_pass,
            "problem": cmp.problem,
            "episodes": len(cmp.episodes),
            "differing_ticks": cmp.differing_ticks,
            "final_equal": cmp.final_equal,
            "first_mismatch": _episode_record(cmp.episodes[0]) if cmp.episodes else None,
            "final_tick_plain": a[-1]["t"] if a else None,
            "final_tick_instr": b[-1]["t"] if b else None,
            "plain_done": res_plain.done,
            "instr_done": res_instr.done,
            "probes_hit": probes_hit if twin_pass else [],
            "wall_s": time.monotonic() - t0,
        }
    except rc_run.CheckError as exc:
        error = str(exc)
    return {
        "name": name,
        "seed": seed,
        "replay": str(replay_path),
        "twin_pass": False,
        "error": error,
        "probes_hit": [],
        "wall_s": time.monotonic() - t0,
    }


def run(
    work_plain: rc_run.Work,
    work_instr: rc_run.Work,
    prepare_instr_fn: Callable[[rc_run.Work], dict],
    out_dir: Path,
    n: int,
    jobs: int,
    seed_start: int,
    mirrors: list[Path],
    time_budget_s: float | None,
    character: str = "Ninja",
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    replays_dir = out_dir / "replays"
    replays_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "matches.jsonl"

    # Resume (operator decision, this session: the first run's task order
    # queued the 5 mirrors behind all 300 random seeds in one shared
    # ThreadPoolExecutor, so the --time-budget cutoff that the brief asks
    # for on a slow host never reached them -- found when that run's own
    # matches.jsonl had 0 "mirror*" entries). Fixed below by submitting
    # the mirrors first. A prior run's matches.jsonl (if present) is read
    # so an already-completed name is not re-run: this lets a second
    # invocation, after the ordering fix, keep every already-finished
    # random match's result and add only the mirrors plus whatever random
    # seeds the first run's cutoff missed.
    done: dict[str, Any] = {}
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            done[rec["name"]] = rec

    t_start = time.monotonic()
    print("preparing the plain (uninstrumented) project...", flush=True)
    prepare_plain(work_plain)
    print("preparing the instrumented project (tools/covsearch/instrument.py)...", flush=True)
    cov_map = prepare_instr_fn(work_instr)
    (work_instr.coverage / "hits").mkdir(parents=True, exist_ok=True)
    (out_dir / "probe_map.json").write_text(json.dumps(cov_map))
    # live_job already attaches ignore.txt via playback_job's own
    # "ignore": ignore_list() (tools/replay_check/run.py). Nothing to
    # add here.

    tasks: list[tuple[str, Path, int | None, Callable[[], Path] | None]] = []
    for i, mirror in enumerate(mirrors):
        name = f"mirror{i}_{mirror.stem}"
        if name not in done:
            tasks.append((name, mirror, None, None))
    for seed in range(seed_start, seed_start + n):
        name = f"free_{seed}"
        if name in done:
            continue
        replay_out = replays_dir / f"{name}.replay"

        def make_generate(seed=seed, name=name, replay_out=replay_out):
            def generate_fn() -> Path:
                job = rc_run.generate_job(
                    character, character, seed, replay_out,
                    work_plain.jobs / f"{name}.gentrace",
                    settings=TOURNAMENT_OVERRIDE,
                )
                rc_run.run_job(work_plain, f"{name}_gen", job)
                return replay_out

            return generate_fn

        tasks.append((name, replay_out, seed, make_generate()))

    results: list[dict[str, Any]] = list(done.values())
    stopped_early = False
    with results_path.open("a") as out_f:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = {
                pool.submit(process_match, work_plain, work_instr, name, replay, seed, gen_fn): name
                for name, replay, seed, gen_fn in tasks
            }
            remaining = set(futures)
            for fut in concurrent.futures.as_completed(futures):
                remaining.discard(fut)
                rec = fut.result()
                results.append(rec)
                out_f.write(json.dumps(rec) + "\n")
                out_f.flush()
                print(
                    f"{rec['name']}: twin_pass={rec['twin_pass']} "
                    f"probes={len(rec.get('probes_hit', []))} "
                    f"wall_s={rec.get('wall_s', 0):.1f} "
                    f"({len(results)}/{len(tasks) + len(done)})",
                    flush=True,
                )
                if time_budget_s is not None and time.monotonic() - t_start > time_budget_s:
                    stopped_early = True
                    break
            if stopped_early:
                for fut in remaining:
                    fut.cancel()

    hit_ids: set[int] = set()
    passed = 0
    failed = []
    for rec in results:
        if rec.get("twin_pass"):
            passed += 1
            hit_ids.update(rec.get("probes_hit", []))
        elif rec.get("seed") is not None or rec["name"].startswith("mirror"):
            failed.append({"name": rec["name"], "seed": rec.get("seed"), "problem": rec.get("problem") or rec.get("error"), "first_mismatch": rec.get("first_mismatch")})

    summary = {
        "n_requested": n,
        "n_mirrors": len(mirrors),
        "n_attempted": len(results),
        "n_twin_pass": passed,
        "n_twin_fail": len(failed),
        "twin_failures": failed,
        "total_probes": cov_map["size"],
        "hit_probes": sorted(hit_ids),
        "wall_s": time.monotonic() - t_start,
        "stopped_early": stopped_early,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
