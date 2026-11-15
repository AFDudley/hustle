"""The covsearch coordinator: frontier, dedup, termination, report.

See tools/covsearch/README.md for the design (steps 6-9) this implements.

A frontier node is a *path*: a root plus the ordered list of (p1 input,
p2 input) choices the search has already kept from that root. To expand a
node, one CovWorker.gd job replays the root plus the path (through
on_action_selected, the live path), lists the real UI's legal buttons
there, builds the discretized candidate list, and branches each
(candidate, candidate) pair with the engine's own `copy_to` -- one job
answers for every candidate pair at that one decision point. The
coordinator never asks a worker to hold state across jobs: each expansion
is a stand-alone process (bridges the "up to 8 Godot processes" design
goal onto simple subprocess parallelism, with the frontier and the probe
map held once, in this process).

Ordering (step 7): the frontier is a priority queue; a node's priority is
the number of probes its branch hit that no kept path has hit yet ("new
probes reached"). Dedup (step 7): a child is dropped when its bucketed
projection (positions and velocities bucketed, state names and tick kept
exact) was already seen.

Termination (step 8): the run stops when every in-scope probe id (from
probe_map.json) has been hit, or the frontier empties first. On a frontier
empty exit, every unseen probe gets a reason in the report: either
"unreachable: <reason>" was supplied for it by name match (a short,
curated table in this module -- lab book facts about what Ninja vs Ninja
can and cannot reach), or, lacking a known reason, "unreached: frontier
exhausted before this probe" -- which is a true statement about the run,
not a claim that the probe is unreachable. tools/covsearch/README.md
records which case the last run left in, and that is not glossed over
here: an "unreached, frontier exhausted" probe is an open item, not a
finding.
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


def run(
    work,
    roots_path: Path,
    out_dir: Path,
    jobs: int,
    time_budget: float,
    run_worker: Callable[[Any, str, dict], Path],
    max_per_player: int = 12,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    probes = load_probe_map(work.root)
    total_ids = set(probes.keys())
    hit_ids: set[int] = set()
    seen_buckets: set[Bucket] = set()
    legality_diffs: list[dict] = []
    kept_paths: list[dict] = []  # {"root": ..., "choices": [...], "new_probes": [...]}
    unreachable: dict[int, str] = {}

    roots = json.loads(roots_path.read_text())
    # frontier: heap of (-new_probe_count, seq, {"root":..., "choices":[...]})
    frontier: list[tuple[int, int, dict]] = []
    for i, root in enumerate(roots):
        heapq.heappush(frontier, (-(10**9), i, {"root": root, "choices": []}))

    start = time.monotonic()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)
    pending: dict[concurrent.futures.Future, dict] = {}

    def submit(node: dict):
        name = f"node{next(_counter)}"
        job = {"base": node["root"], "choices": node["choices"], "max_per_player": max_per_player}
        fut = executor.submit(run_worker, work, name, job)
        pending[fut] = node

    # Prime up to `jobs` expansions.
    while frontier and len(pending) < jobs:
        _, _, node = heapq.heappop(frontier)
        submit(node)

    while pending:
        if total_ids and total_ids <= hit_ids:
            break
        if time_budget and time.monotonic() - start > time_budget:
            break
        done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
        for fut in done:
            node = pending.pop(fut)
            out_path = fut.result()
            for line in out_path.read_text().splitlines():
                rec = json.loads(line)
                if rec["kind"] == "legality_diff":
                    legality_diffs.append(rec)
                    continue
                if rec["kind"] != "branch":
                    continue
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
            # refill
            if frontier and (not time_budget or time.monotonic() - start <= time_budget) and not (total_ids and total_ids <= hit_ids):
                _, _, nxt = heapq.heappop(frontier)
                submit(nxt)
    executor.shutdown(wait=False, cancel_futures=True)

    missing = total_ids - hit_ids
    for pid in missing:
        rec = probes[pid]
        reason = None
        for substr, forced in STATIC_UNREACHABLE:
            if substr in rec["file"] and forced:
                reason = forced
        unreachable[pid] = reason or "unreached: frontier exhausted before this probe in this run"

    report = {
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
        "kept_paths": len(kept_paths),
        "frontier_remaining": len(frontier),
        "wall_time_s": time.monotonic() - start,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "kept_paths.json").write_text(json.dumps(kept_paths, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k not in ("unreachable", "legality_diffs")}, indent=2))
    return 0
