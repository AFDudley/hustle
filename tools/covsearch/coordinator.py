"""The covsearch coordinator: frontier, dedup, termination, report.

See tools/covsearch/README.md for the design (steps 6-9) this implements.
Operator decision: no ghost branching. `CovWorker.gd`'s branch jobs replay
every branch on a fresh, real (non-ghost) match, live, from tick 0, so the
probes a branch record reports are already exact; this module credits
them directly, with no separate ghost/exact split and no divergence check
to run afterward.

A frontier node is a *path*: a root plus the ordered list of (p1 input,
p2 input) choices the search has already kept from that root. Expanding a
node is two kinds of CovWorker.gd job, never two live Games at once in one
process (operator decision, see CovWorker.gd header comment):

1. One `list_candidates` job replays the root plus the path (through
   on_action_selected, the live path) once, lists the real UI's legal
   buttons there, and writes the discretized candidate list for each
   player (design step 5).
2. The (p1 candidate, p2 candidate) pairs this produces are split into up
   to `--jobs` batches. Each batch runs as one `branch_batch` job: a
   single Godot process that, for each of its pairs in turn, rebuilds the
   whole root-plus-path from scratch on a fresh Game, applies that one
   pair, and records the probe delta and a projection -- one Game at a
   time, never two concurrently, the old Game freed completely before the
   next one is built (CovWorker.gd `_build_root_and_choices`). The
   batches run in parallel, up to `--jobs` Godot processes at once; a
   batch's own pairs run strictly in sequence inside that one process
   (the brief's named optimisation for amortising process-startup cost
   over several branches -- see README.md "Cost measurement").

The coordinator processes one frontier node at a time (so the `--jobs`
Godot processes all serve that node's own candidate pairs), not several
frontier nodes in parallel: this keeps the one frontier and the one
hit_ids/seen_buckets state in this process free of any cross-node race,
at the cost of not overlapping one node's list_candidates job with
another node's branch_batch jobs. Each node's own fan-out already uses
every core, which is where the parallelism budget goes.

Ordering (step 7): the frontier is a priority queue; a node's priority is
the number of probes its branch hit that no kept path has hit yet ("new
probes reached"). Dedup (step 7): a child is dropped when its bucketed
projection (positions and velocities bucketed, state names and tick kept
exact) was already seen.

Termination (step 8): the run stops when every in-scope probe id (from
probe_map.json) has been hit, or the frontier empties first, or
--time-budget expires. `report.json["termination_reason"]` names which of
the three happened, so a time-budget stop is reported as such, not as
converged. On a frontier-empty or time-budget exit, every unhit probe
gets a reason in the report: either "unreachable: <reason>" was supplied
for it by name match (a short, curated table in this module -- lab book
facts about what Ninja vs Ninja can and cannot reach), or, lacking a
known reason, "unreached: frontier exhausted before this probe" (or the
time-budget equivalent) -- a true statement about the run, not a claim
that the probe is unreachable.
"""

from __future__ import annotations

import concurrent.futures
import heapq
import itertools
import json
import time
from pathlib import Path
from typing import Any, Callable

Bucket = tuple

# Decision-point counter for the unique names given to worker jobs.
_counter = itertools.count()


def bucket_projection(projection: dict) -> Bucket:
    """Reduce a projection to the abstract state used for dedup (step 7)."""
    items = []
    for name in sorted(projection.get("objs", {})):
        obj = projection["objs"][name]
        pos = obj.get("pos", {})
        vel = obj.get("vel", {})

        def b(v, grid):
            try:
                return round(float(v) / grid) * grid
            except (TypeError, ValueError):
                return v

        items.append((
            name,
            obj.get("state"),
            b(pos.get("x"), 8), b(pos.get("y"), 8),
            b(vel.get("x"), 1), b(vel.get("y"), 1),
            obj.get("hp"), obj.get("meter"), obj.get("burst"),
        ))
    return (projection.get("game_over"), tuple(items))


def load_probe_map(work_root: Path) -> dict[int, dict]:
    raw = json.loads((work_root / "probe_map.json").read_text())
    probes: dict[int, dict] = {}
    for res_path, records in raw["files"].items():
        for rec in records:
            probes[rec["id"]] = {**rec, "file": res_path}
    return probes


# Known static exclusions (step 8): a probe id's file/kind/detail match one
# of these gives its written unreachable reason without spending a search
# step on it. Each entry is (file substring, reason).
STATIC_UNREACHABLE = [
    ("characters/stickman/", None),  # Ninja's own file: never unreachable by name alone.
]


def _update_readme_progress(readme_path: Path, progress: dict) -> None:
    """Rewrite the README's live-progress block in place, so a stop loses
    no progress (the brief's own requirement). Only touches the text
    between the two markers; every other word of the README is untouched.
    `container.sh` mounts this one file read-write for exactly this call
    (see container.sh for why the rest of the repository stays read-only
    while a worker runs)."""
    text = readme_path.read_text()
    begin, end = "<!-- LIVE-PROGRESS:BEGIN -->", "<!-- LIVE-PROGRESS:END -->"
    if begin not in text or end not in text:
        return
    pre, rest = text.split(begin, 1)
    _, post = rest.split(end, 1)
    body = json.dumps(progress, indent=2)
    readme_path.write_text(f"{pre}{begin}\n```json\n{body}\n```\n{end}{post}")


def _chunks(items: list, n: int) -> list[list]:
    """Split `items` into at most `n` nearly-equal, non-empty chunks."""
    if not items:
        return []
    n = max(1, min(n, len(items)))
    size = -(-len(items) // n)  # ceil division
    return [items[i:i + size] for i in range(0, len(items), size)]


def run(
    work,
    roots_path: Path,
    out_dir: Path,
    jobs: int,
    time_budget: float,
    run_worker: Callable[[Any, str, dict], Path],
    max_per_player: int = 12,
    readme_path: Path | None = None,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    probes = load_probe_map(work.root)
    total_ids = set(probes.keys())
    hit_ids: set[int] = set()
    seen_buckets: set[Bucket] = set()
    legality_diffs: list[dict] = []
    kept_paths: list[dict] = []  # {"root": ..., "choices": [...], "probes": [...]}
    unreachable: dict[int, str] = {}
    total_branches = 0
    total_ticks_simulated = 0  # sum of every branch's own final tick
    branch_job_seconds: list[float] = []  # one entry per branch_batch job
    branch_job_pairs: list[int] = []
    stalled_nodes: list[dict] = []  # README.md "Blocking defect": nodes dropped after a worker stall

    roots = json.loads(roots_path.read_text())
    # frontier: heap of (-new_probe_count, seq, {"root":..., "choices":[...]})
    frontier: list[tuple[int, int, dict]] = []
    for i, root in enumerate(roots):
        heapq.heappush(frontier, (-(10**9), i, {"root": root, "choices": []}))

    start = time.monotonic()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)

    jobs_done = 0
    progress_path = out_dir / "progress.json"
    last_readme_write = 0.0

    def write_progress():
        nonlocal last_readme_write
        elapsed = time.monotonic() - start
        progress = {
            "jobs_done": jobs_done,
            "hit_count": len(hit_ids),
            "total_probes": len(total_ids),
            "kept_paths": len(kept_paths),
            "frontier_size": len(frontier),
            "legality_diffs": len(legality_diffs),
            "stalled_nodes": len(stalled_nodes),
            "branches_recorded": total_branches,
            "ticks_simulated": total_ticks_simulated,
            "branches_per_second": total_branches / elapsed if elapsed > 0 else 0.0,
            "ticks_per_second": total_ticks_simulated / elapsed if elapsed > 0 else 0.0,
        }
        progress_path.write_text(json.dumps(progress, indent=2))
        now = time.monotonic()
        if readme_path is not None and now - last_readme_write >= 90.0:
            _update_readme_progress(readme_path, progress)
            last_readme_write = now

    def time_up() -> bool:
        return bool(time_budget) and time.monotonic() - start > time_budget

    termination_reason = "frontier_exhausted"
    while frontier:
        if total_ids and total_ids <= hit_ids:
            termination_reason = "all_probes_hit"
            break
        if time_up():
            termination_reason = "time_budget_stopped"
            break
        _, _, node = heapq.heappop(frontier)

        # Phase 1: list this node's candidates (one Godot process, one Game).
        list_name = f"list{next(_counter)}"
        list_job = {
            "base": node["root"], "choices": node["choices"],
            "action": "list_candidates", "max_per_player": max_per_player,
        }
        try:
            list_out = run_worker(work, list_name, list_job)
        except Exception as exc:
            if type(exc).__name__ != "WorkerStalled":
                raise
            # README.md "Blocking defect": driving a replay_prefix root
            # live can stall game.gd's own tick() with no code change
            # available in this tool's scope (committed game files are off
            # limits). Named here, not hidden: the node is dropped, its
            # parent's own credit stands, and the search moves on.
            stalled_nodes.append({"node": node, "phase": "list_candidates", "reason": str(exc)})
            if jobs_done % 10 == 0:
                write_progress()
            continue
        jobs_done += 1
        p1_candidates: list[dict] = []
        p2_candidates: list[dict] = []
        for line in list_out.read_text().splitlines():
            rec = json.loads(line)
            if rec["kind"] == "legality_diff":
                legality_diffs.append(rec)
            elif rec["kind"] == "candidates":
                p1_candidates = rec["p1"]
                p2_candidates = rec["p2"]
        pairs = [[p1, p2] for p1 in p1_candidates for p2 in p2_candidates]
        # A player with no real options gets one synthetic placeholder
        # entry from CovWorker.gd _candidates: {"action":"Continue",
        # "extra":null}. If both players are in that state, this node is
        # not a real decision (the fixed up_to_tick root can land on such
        # a tick; see README.md "Known defect") and CovWorker.gd
        # _apply_pair_live already refuses to send that placeholder as an
        # input -- so branching it would add nothing. Skip it; its own
        # coverage was already credited by the parent branch that reached
        # it.
        def is_placeholder(c: dict) -> bool:
            return c.get("action") == "Continue" and c.get("extra") is None
        if all(is_placeholder(p1) for p1 in p1_candidates) and all(is_placeholder(p2) for p2 in p2_candidates):
            if jobs_done % 10 == 0:
                write_progress()
            continue
        if not pairs:
            if jobs_done % 10 == 0:
                write_progress()
            continue

        # Phase 2: branch every pair, batched across up to `jobs` processes,
        # each batch's own pairs run strictly one Game at a time in sequence
        # (CovWorker.gd `_branch_batch`; see module docstring).
        batches = _chunks(pairs, jobs)
        futures = {}
        for batch in batches:
            name = f"branch{next(_counter)}"
            job = {
                "base": node["root"], "choices": node["choices"],
                "action": "branch_batch", "pairs": batch,
            }
            fut = executor.submit(run_worker, work, name, job)
            futures[fut] = (name, len(batch), batch)
        for fut in concurrent.futures.as_completed(futures):
            name, batch_size, batch = futures[fut]
            t0 = time.monotonic()
            try:
                out_path = fut.result()
            except Exception as exc:
                if type(exc).__name__ != "WorkerStalled":
                    raise
                stalled_nodes.append({"node": node, "phase": "branch_batch", "pairs": batch, "reason": str(exc)})
                continue
            branch_job_seconds.append(time.monotonic() - t0)
            branch_job_pairs.append(batch_size)
            jobs_done += 1
            for line in out_path.read_text().splitlines():
                rec = json.loads(line)
                if rec["kind"] != "branch":
                    continue
                total_branches += 1
                total_ticks_simulated += int(rec["projection"].get("tick", 0))
                for pid in rec["probes"]:
                    hit_ids.add(pid)
                bucket = bucket_projection(rec["projection"])
                is_new_state = bucket not in seen_buckets
                if is_new_state:
                    seen_buckets.add(bucket)
                child = {
                    "root": node["root"],
                    "choices": node["choices"] + [[rec["p1_input"], rec["p2_input"]]],
                }
                score = len(rec["probes"])
                if is_new_state and not rec["projection"].get("game_over"):
                    heapq.heappush(frontier, (-score, next(_counter), child))
                if is_new_state:
                    kept_paths.append({**child, "probes": rec["probes"]})
        if jobs_done % 10 == 0:
            write_progress()
        if time_up():
            termination_reason = "time_budget_stopped"
            break

    write_progress()
    executor.shutdown(wait=False, cancel_futures=True)

    missing = total_ids - hit_ids
    if termination_reason == "time_budget_stopped":
        default_reason = "unreached: time budget expired before this probe in this run, with the frontier still non-empty"
    else:
        default_reason = "unreached: frontier exhausted before this probe in this run"
    for pid in missing:
        rec = probes[pid]
        reason = None
        for substr, forced in STATIC_UNREACHABLE:
            if substr in rec["file"] and forced:
                reason = forced
        unreachable[pid] = reason or default_reason

    elapsed = time.monotonic() - start
    seconds_per_branch_job = (
        sum(branch_job_seconds) / len(branch_job_seconds) if branch_job_seconds else 0.0
    )
    seconds_per_branch = (
        sum(branch_job_seconds) / sum(branch_job_pairs) if branch_job_pairs else 0.0
    )
    report = {
        "termination_reason": termination_reason,
        "probe_totals": {
            "line": sum(1 for r in probes.values() if r["kind"] == "line"),
            "branch": sum(1 for r in probes.values() if r["kind"] in ("branch_true", "branch_false")),
            "value_class": sum(1 for r in probes.values() if r["kind"] == "value_class"),
            "all": len(total_ids),
        },
        "hit": sorted(hit_ids),
        "hit_count": len(hit_ids),
        "unreachable_count": len(missing),
        "unreachable": {str(pid): {"reason": unreachable[pid], **probes[pid]} for pid in sorted(missing)},
        "legality_diffs": legality_diffs,
        "stalled_nodes": stalled_nodes,
        "kept_paths": len(kept_paths),
        "frontier_remaining": len(frontier),
        "branches_recorded": total_branches,
        "ticks_simulated": total_ticks_simulated,
        "branches_per_second": total_branches / elapsed if elapsed > 0 else 0.0,
        "ticks_per_second": total_ticks_simulated / elapsed if elapsed > 0 else 0.0,
        "branch_batch_jobs": len(branch_job_seconds),
        "seconds_per_branch_batch_job": seconds_per_branch_job,
        "seconds_per_branch": seconds_per_branch,
        "wall_time_s": elapsed,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "kept_paths.json").write_text(json.dumps(kept_paths, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k not in ("unreachable", "legality_diffs", "stalled_nodes")}, indent=2))
    return 0
