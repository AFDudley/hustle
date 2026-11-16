"""tools/covsearch biased: coverage-biased random play over all 25 pairings.

Free coverage (free_coverage.py) showed that uniform random play saturates
after about 10 matches per pairing, and that mirror matches add little shared
coverage. This driver spends each match where coverage is still missing:

1. Scope: the shared simulation code plus all five characters' own
   directories (instrument.set_all_characters), so one probe map covers
   every pairing.
2. Round 0: every recorded mirror replay (free_coverage.MIRRORS_BY_CHARACTER).
3. Each later round: 8 matches (one per core). Each match picks a pairing
   with weight 1 + unhit probes in the two characters' own directories, and
   generates the match on the INSTRUMENTED project with tournament settings
   and `action_weights`: each state's weight is 1 + the unhit probes in its
   script, so the generator chooses moves whose code is not yet covered.
4. Twin rule (operator decision): the generated match is replayed on the
   PLAIN project through the live path; its probe hits count only if the
   generate trace and the plain live trace are identical for the whole match
   (tools/replay_check compare_traces, ignore.txt). Two Godot runs per match.
5. The weights are rebuilt from the union of hits after every round.
6. End: two consecutive rounds that add no new probe. The rest is the residue
   for hand construction; this driver does not claim it unreachable.

Runs only inside tools/replay_check's container, imported by
tools/covsearch/run.py (which puts tools/replay_check on sys.path as "run").
"""

from __future__ import annotations

import concurrent.futures
import json
import random
import re
import time
from pathlib import Path
from typing import Any

import run as rc_run  # tools/replay_check/run.py

import free_coverage
import instrument

CHARACTERS = list(instrument.CHARACTER_DIRS)
SCENES = {
    "Ninja": "characters/stickman/NinjaGuy.tscn",
    "Cowboy": "characters/swordandgun/SwordGuy.tscn",
    "Wizard": "characters/wizard/Wizard.tscn",
    "Robot": "characters/robo/Robot.tscn",
    "Mutant": "characters/mutant/Mutant.tscn",
}
BASE_SCENE = "characters/BaseChar.tscn"
EXT_RE = re.compile(r'\[ext_resource path="([^"]+)" type="Script" id=(\d+)\]')
NODE_RE = re.compile(r'^\[node name="([^"]+)"[^\]]*parent="StateMachine"[^\]]*\]$')
SCRIPT_RE = re.compile(r"^script = ExtResource\( (\d+) \)$")
STALL_ROUNDS = 2


def state_scripts(repo: Path, scene: str) -> dict[str, str]:
    """State node name -> script res path, for one scene file."""
    text = (repo / scene).read_text()
    ext = {i: p for p, i in EXT_RE.findall(text)}
    out: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        m = NODE_RE.match(line)
        if m:
            current = m.group(1)
            continue
        if line.startswith("["):
            current = None
            continue
        if current is not None:
            s = SCRIPT_RE.match(line)
            if s and s.group(1) in ext:
                out[current] = ext[s.group(1)]
    return out


def character_states(repo: Path) -> dict[str, dict[str, str]]:
    base = state_scripts(repo, BASE_SCENE)
    return {c: {**base, **state_scripts(repo, SCENES[c])} for c in CHARACTERS}


def file_probes(cov_map: dict) -> dict[str, list[int]]:
    return {res: [p["id"] for p in probes] for res, probes in cov_map["files"].items()}


def weights_for(states: dict[str, str], probes_by_file: dict[str, list[int]], hit: set[int]) -> dict[str, float]:
    out = {}
    for name, script in states.items():
        unhit = sum(1 for pid in probes_by_file.get(script, []) if pid not in hit)
        out[name] = 1.0 + unhit
    return out


def own_unhit(character: str, probes_by_file: dict[str, list[int]], hit: set[int]) -> int:
    prefix = f"res://characters/{instrument.CHARACTER_DIRS[character]}/"
    return sum(1 for res, ids in probes_by_file.items() if res.startswith(prefix) for pid in ids if pid not in hit)


def twin_credit(work_instr, name: str, trace_a: Path, trace_b: Path, hits_job: str) -> dict[str, Any]:
    a = rc_run.read_trace(trace_a)
    b = rc_run.read_trace(trace_b)
    cmp = rc_run.compare_traces(a, b)
    hits_path = work_instr.coverage / "hits" / f"{hits_job}.json"
    probes = [pid for pid, _ in json.loads(hits_path.read_text())] if hits_path.exists() else []
    return {
        "twin_pass": cmp.ok,
        "problem": cmp.problem,
        "first_mismatch": {"tick": cmp.episodes[0].tick, "parts": cmp.episodes[0].parts} if cmp.episodes else None,
        "probes_hit": probes if cmp.ok else [],
        "final_tick": a[-1]["t"] if a else None,
    }


def recorded_match(work_plain, work_instr, name: str, replay: Path) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        ta = work_instr.jobs / f"{name}.instr.trace"
        tb = work_plain.jobs / f"{name}.plain.trace"
        rc_run.run_job(work_instr, f"{name}_instr", rc_run.live_job(replay, ta))
        rc_run.run_job(work_plain, f"{name}_plain", rc_run.live_job(replay, tb))
        rec = twin_credit(work_instr, name, ta, tb, f"{name}_instr")
    except rc_run.CheckError as exc:
        rec = {"twin_pass": False, "error": str(exc), "probes_hit": []}
    return {"name": name, "replay": str(replay), "wall_s": time.monotonic() - t0, **rec}


def generated_match(work_plain, work_instr, out_dir: Path, name: str, p1: str, p2: str, seed: int, weights: dict[str, dict[str, float]]) -> dict[str, Any]:
    t0 = time.monotonic()
    replay = out_dir / "replays" / f"{name}.replay"
    try:
        ta = work_instr.jobs / f"{name}.gen.trace"
        tb = work_plain.jobs / f"{name}.plain.trace"
        # One action_weights table serves both players: a state name means the
        # same script within one character, and two characters' tables are
        # merged with the higher weight kept, so each player's own moves keep
        # their own bias.
        table: dict[str, float] = dict(weights[p1])
        for k, v in weights[p2].items():
            table[k] = max(table.get(k, 0.0), v)
        job = rc_run.generate_job(p1, p2, seed, replay, ta, settings=free_coverage.TOURNAMENT_OVERRIDE, action_weights=table)
        rc_run.run_job(work_instr, f"{name}_gen", job)
        rc_run.run_job(work_plain, f"{name}_plain", rc_run.live_job(replay, tb))
        rec = twin_credit(work_instr, name, ta, tb, f"{name}_gen")
    except rc_run.CheckError as exc:
        rec = {"twin_pass": False, "error": str(exc), "probes_hit": []}
    return {"name": name, "p1": p1, "p2": p2, "seed": seed, "replay": str(replay), "wall_s": time.monotonic() - t0, **rec}


def run(work_root: Path, prepare_instr_fn, out_dir: Path, jobs: int, repo: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "replays").mkdir(exist_ok=True)
    results_path = out_dir / "matches.jsonl"
    rounds_path = out_dir / "rounds.jsonl"
    work_plain = rc_run.Work(root=work_root / "plain")
    work_instr = rc_run.Work(root=work_root / "instr", coverage=work_root / "instr_coverage")

    t_start = time.monotonic()
    rc_run.prepare(work_plain)
    instrument.set_all_characters()
    cov_map = prepare_instr_fn(work_instr)
    (work_instr.coverage / "hits").mkdir(parents=True, exist_ok=True)
    (out_dir / "probe_map.json").write_text(json.dumps(cov_map))
    probes_by_file = file_probes(cov_map)
    states = character_states(repo)

    done: dict[str, dict] = {}
    if results_path.exists():
        for line in results_path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done[rec["name"]] = rec
    hit: set[int] = set()
    for rec in done.values():
        if rec.get("twin_pass"):
            hit.update(rec["probes_hit"])

    def record(rec: dict, out_f) -> int:
        done[rec["name"]] = rec
        out_f.write(json.dumps(rec) + "\n")
        out_f.flush()
        before = len(hit)
        if rec.get("twin_pass"):
            hit.update(rec["probes_hit"])
        print(f"{rec['name']}: twin_pass={rec.get('twin_pass')} new={len(hit) - before} total={len(hit)}/{cov_map['size']} wall_s={rec.get('wall_s', 0):.0f}", flush=True)
        return len(hit) - before

    stall = 0
    round_no = 0
    with results_path.open("a") as out_f, concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        tasks = []
        for c, replays in free_coverage.MIRRORS_BY_CHARACTER.items():
            for i, r in enumerate(replays):
                name = f"rec_{c}_{i}"
                if name not in done:
                    tasks.append(pool.submit(recorded_match, work_plain, work_instr, name, r))
        r0_new = sum(record(f.result(), out_f) for f in concurrent.futures.as_completed(tasks))
        rounds_log = rounds_path.open("a")
        rounds_log.write(json.dumps({"round": 0, "new": r0_new, "total": len(hit), "elapsed_s": time.monotonic() - t_start}) + "\n")
        rounds_log.flush()
        while stall < STALL_ROUNDS:
            round_no += 1
            names = [f"b{round_no}_{i}" for i in range(jobs)]
            if all(n in done for n in names):
                continue
            weights = {c: weights_for(states[c], probes_by_file, hit) for c in CHARACTERS}
            rng = random.Random(round_no)
            char_w = [1 + own_unhit(c, probes_by_file, hit) for c in CHARACTERS]
            tasks = []
            for i, name in enumerate(names):
                if name in done:
                    continue
                p1, p2 = rng.choices(CHARACTERS, weights=char_w, k=2)
                tasks.append(pool.submit(generated_match, work_plain, work_instr, out_dir, name, p1, p2, round_no * 1000 + i, weights))
            new = sum(record(f.result(), out_f) for f in concurrent.futures.as_completed(tasks))
            stall = stall + 1 if new == 0 else 0
            rounds_log.write(json.dumps({"round": round_no, "new": new, "total": len(hit), "elapsed_s": time.monotonic() - t_start}) + "\n")
            rounds_log.flush()

    summary = {
        "total_probes": cov_map["size"],
        "hit_count": len(hit),
        "hit_probes": sorted(hit),
        "rounds": round_no,
        "matches": len(done),
        "twin_pass": sum(1 for r in done.values() if r.get("twin_pass")),
        "twin_fail": [{"name": r["name"], "error": r.get("error"), "problem": r.get("problem"), "first_mismatch": r.get("first_mismatch")} for r in done.values() if not r.get("twin_pass")],
        "end": f"{STALL_ROUNDS} consecutive rounds with no new probe",
        "wall_s": time.monotonic() - t_start,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
