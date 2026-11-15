"""The covsearch coordinator: frontier, dedup, termination, report.

See tools/covsearch/README.md for the design (steps 6-9) this implements,
and its "Decision" section for the hybrid exploration/credit split this
module enforces: ghost `copy_to` branching only ever *proposes* a path;
only an exact re-run of that same path, from tick 0 on the real,
non-ghost live path, in a fresh process, earns that path's probes any
credit.

A frontier node is a *path*: a root plus the ordered list of (p1 input,
p2 input) choices the search has already kept from that root, plus the
chain of ghost projections recorded at each of those choices. To expand
a node, one CovWorker.gd job replays the root plus the path (through
on_action_selected, the live path), lists the real UI's legal buttons
there, builds the discretized candidate list, and branches each
(candidate, candidate) pair with the engine's own `copy_to` -- one job
answers for every candidate pair at that one decision point. The
coordinator never asks a worker to hold state across jobs: each expansion
is a stand-alone process (bridges the "up to 8 Godot processes" design
goal onto simple subprocess parallelism, with the frontier and the probe
map held once, in this process).

Ordering (step 7): the ghost frontier is a priority queue; a node's
priority is the number of probes its ghost branch hit. Dedup (step 7):
a ghost child is dropped from the frontier when its bucketed projection
(positions and velocities bucketed, state names and tick kept exact) was
already seen; it is still queued once for an exact re-run.

Exact credit: every ghost-kept path (one per distinct new bucket) is
queued for one exact re-run -- the same path's root and choices, replayed
from tick 0 on the real `game` in a fresh process. `jobs` worker slots
are shared between ghost expansion and exact re-runs; an exact re-run
takes priority when both queues have work, so credit keeps moving instead
of piling up behind an ever-growing ghost frontier. Only the exact run's
own cumulative probe set is added to `hit_ids`; a ghost branch's own
probe count only ever sets its frontier priority, never a probe's credit.
Each exact run's per-decision projection is compared against the ghost
run's projection at the same decision (`first_divergence`); a mismatch is
recorded with its first differing step, tick and field, and does not
block that exact run's own probes from being credited (they are real,
regardless of whether the ghost run that proposed the path predicted them
correctly).

Termination (step 8): the run stops when every in-scope probe id (from
probe_map.json) has exact credit, or both the ghost frontier and the
exact-verify queue empty first. On that second kind of exit, every probe
without exact credit gets a reason in the report: either "unreachable:
<reason>" was supplied for it by name match (a short, curated table in
this module -- lab book facts about what Ninja vs Ninja can and cannot
reach), or, lacking a known reason, "unreached: frontier exhausted before
this probe in this run" -- which is a true statement about the run, not a
claim that the probe is unreachable. tools/covsearch/README.md records
which case the last run left in, and that is not glossed over here: an
"unreached, frontier exhausted" probe is an open item, not a finding.
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


STATIC_UNREACHABLE = [
    ("characters/stickman/", None),  # Ninja's own file: never unreachable by name alone.
]


def flatten_projection(projection: dict) -> list[tuple[str, Any]]:
    """Flatten one decision point's projection to (field, value) pairs, for
    a field-by-field divergence comparison (not the rounded `bucket_projection`
    dedup key, which would hide a real divergence under its own rounding)."""
    items: list[tuple[str, Any]] = [
        ("tick", projection.get("tick")),
        ("game_over", projection.get("game_over")),
        ("p1_turn", projection.get("p1_turn")),
        ("p2_turn", projection.get("p2_turn")),
    ]
    for name in sorted(projection.get("objs", {})):
        obj = projection["objs"][name]
        for field in ("pos", "vel", "state", "hp", "meter", "burst", "hitlag", "hitstun"):
            value = obj.get(field)
            if isinstance(value, dict):
                for sub in ("x", "y"):
                    items.append((f"{name}.{field}.{sub}", value.get(sub)))
            else:
                items.append((f"{name}.{field}", value))
    return items


def first_divergence(ghost_projections: list[dict], exact_steps: list[dict]) -> dict | None:
    """Compare a kept path's ghost-branch projection at each decision point
    against the same path's exact re-run projection at the same point
    (README.md "Decision": hybrid branching). Returns the first differing
    field, with its tick, or None when every compared step matches. A
    length mismatch (the exact run reached game-over, or a decision point,
    at a different step than the ghost run) is itself reported, at the
    first step past the shorter run."""
    n = min(len(ghost_projections), len(exact_steps))
    for i in range(n):
        ghost_fields = dict(flatten_projection(ghost_projections[i]))
        exact_fields = dict(flatten_projection(exact_steps[i]["projection"]))
        for key, ghost_value in ghost_fields.items():
            if key not in exact_fields:
                continue
            if ghost_value != exact_fields[key]:
                return {
                    "step": i, "tick": exact_steps[i]["tick"], "field": key,
                    "ghost": ghost_value, "exact": exact_fields[key],
                }
    if len(ghost_projections) != len(exact_steps):
        return {
            "step": n, "tick": exact_steps[n]["tick"] if n < len(exact_steps) else None,
            "field": "step_count", "ghost": len(ghost_projections), "exact": len(exact_steps),
        }
    return None


def _update_readme_progress(readme_path: Path, progress: dict, divergences: list[dict]) -> None:
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
    extra = ""
    if divergences:
        extra = (
            f"\n\nGhost/live projection divergences found so far: {len(divergences)}. "
            "The first one (root, choices, first differing step/tick/field):\n"
            f"```json\n{json.dumps(divergences[0], indent=2)}\n```"
        )
    readme_path.write_text(f"{pre}{begin}\n```json\n{body}\n```{extra}\n{end}{post}")


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
    hit_ids: set[int] = set()  # exact-confirmed credit only (README.md "Decision")
    ghost_hit_union: set[int] = set()  # probes any ghost branch proposed, for the report
    seen_buckets: set[Bucket] = set()
    legality_diffs: list[dict] = []
    divergences: list[dict] = []
    exact_costs: list[float] = []  # seconds per 1000 ticks, one entry per exact job
    exact_confirmed_paths: list[dict] = []  # the only thing written to kept_paths.json
    ghost_kept_count = 0
    unreachable: dict[int, str] = {}

    roots = json.loads(roots_path.read_text())
    # Ghost frontier: a heap of (-new_probe_count, seq, node). `node` carries
    # its own ghost-branch projection chain, so an exact-queue entry built
    # from it can be compared tick-for-tick against its exact re-run later.
    frontier: list[tuple[int, int, dict]] = []
    for i, root in enumerate(roots):
        heapq.heappush(frontier, (-(10**9), i, {"root": root, "choices": [], "ghost_projections": []}))
    # Exact-verify queue: one entry per distinct ghost-kept path, FIFO.
    # Scheduling gives this queue priority over the ghost frontier (README.md
    # "queue each kept path for an exact re-run, batch them across the 8
    # processes"), so exact credit keeps moving instead of piling up behind
    # an ever-growing ghost frontier.
    exact_queue: list[dict] = []

    start = time.monotonic()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)
    pending: dict[concurrent.futures.Future, dict] = {}

    def submit_ghost(node: dict):
        name = f"node{next(_counter)}"
        job = {"base": node["root"], "choices": node["choices"], "max_per_player": max_per_player}
        fut = executor.submit(run_worker, work, name, job)
        pending[fut] = {"type": "ghost", "node": node}

    def submit_exact(entry: dict):
        name = f"exact{next(_counter)}"
        job = {"base": entry["root"], "choices": entry["choices"], "exact": True}
        fut = executor.submit(run_worker, work, name, job)
        pending[fut] = {"type": "exact", "entry": entry, "t0": time.monotonic()}

    def refill():
        while len(pending) < jobs:
            if exact_queue:
                submit_exact(exact_queue.pop(0))
            elif frontier:
                _, _, node = heapq.heappop(frontier)
                submit_ghost(node)
            else:
                return

    refill()

    jobs_done = 0
    progress_path = out_dir / "progress.json"
    last_readme_write = 0.0

    def write_progress():
        nonlocal last_readme_write
        progress = {
            "jobs_done": jobs_done,
            "exact_confirmed_probes": len(hit_ids),
            "ghost_proposed_probes": len(ghost_hit_union),
            "total_probes": len(total_ids),
            "kept_paths_ghost_proposed": ghost_kept_count,
            "kept_paths_exact_confirmed": len(exact_confirmed_paths),
            "frontier_size": len(frontier),
            "exact_queue_size": len(exact_queue),
            "legality_diffs": len(legality_diffs),
            "divergence_count": len(divergences),
            "elapsed_s": time.monotonic() - start,
        }
        progress_path.write_text(json.dumps(progress, indent=2))
        now = time.monotonic()
        if readme_path is not None and now - last_readme_write >= 90.0:
            _update_readme_progress(readme_path, progress, divergences)
            last_readme_write = now

    while pending:
        if total_ids and total_ids <= hit_ids:
            break
        if time_budget and time.monotonic() - start > time_budget:
            break
        done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
        for fut in done:
            item = pending.pop(fut)
            jobs_done += 1
            out_path = fut.result()
            if item["type"] == "ghost":
                node = item["node"]
                for line in out_path.read_text().splitlines():
                    rec = json.loads(line)
                    if rec["kind"] == "legality_diff":
                        legality_diffs.append(rec)
                        continue
                    if rec["kind"] != "branch":
                        continue
                    ghost_hit_union.update(rec["probes"])
                    bucket = bucket_projection(rec["projection"])
                    is_new_state = bucket not in seen_buckets
                    if is_new_state:
                        seen_buckets.add(bucket)
                    child = {
                        "root": node["root"],
                        "choices": node["choices"] + [[rec["p1_input"], rec["p2_input"]]],
                        "ghost_projections": node["ghost_projections"] + [rec["projection"]],
                    }
                    if is_new_state:
                        ghost_kept_count += 1
                        exact_queue.append(child)
                        if not rec["projection"].get("game_over"):
                            score = len(rec["probes"])
                            heapq.heappush(frontier, (-score, next(_counter), child))
            else:  # "exact": one re-run of a ghost-kept path on the real live path
                entry = item["entry"]
                exact_steps = []
                exact_result = None
                for line in out_path.read_text().splitlines():
                    rec = json.loads(line)
                    if rec["kind"] == "exact_step":
                        exact_steps.append(rec)
                    elif rec["kind"] == "exact_result":
                        exact_result = rec
                if exact_result is None:
                    raise RuntimeError(f"exact re-run produced no exact_result; see {out_path}")
                hit_ids.update(exact_result["hit_ids"])
                exact_confirmed_paths.append({
                    "root": entry["root"], "choices": entry["choices"],
                    "probes": exact_result["hit_ids"],
                })
                final_tick = exact_result["final_tick"]
                if final_tick > 0:
                    exact_costs.append((time.monotonic() - item["t0"]) / final_tick * 1000.0)
                divergence = first_divergence(entry["ghost_projections"], exact_steps)
                if divergence is not None:
                    divergences.append({"root": entry["root"], "choices": entry["choices"], **divergence})
        if not (total_ids and total_ids <= hit_ids) and not (time_budget and time.monotonic() - start > time_budget):
            refill()
        if jobs_done % 10 == 0:
            write_progress()
    write_progress()
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
        "kept_paths_ghost_proposed": ghost_kept_count,
        "kept_paths_exact_confirmed": len(exact_confirmed_paths),
        "ghost_proposed_probe_count": len(ghost_hit_union),
        "divergence_count": len(divergences),
        "divergences": divergences,
        "exact_rerun_cost_s_per_1000_ticks": {
            "n": len(exact_costs),
            "mean": sum(exact_costs) / len(exact_costs) if exact_costs else None,
            "min": min(exact_costs) if exact_costs else None,
            "max": max(exact_costs) if exact_costs else None,
        },
        "frontier_remaining": len(frontier),
        "exact_queue_remaining": len(exact_queue),
        "wall_time_s": time.monotonic() - start,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "kept_paths.json").write_text(json.dumps(exact_confirmed_paths, indent=2))
    summary_keys = ("probe_totals", "hit_count", "unreachable_count", "kept_paths_ghost_proposed",
                     "kept_paths_exact_confirmed", "ghost_proposed_probe_count", "divergence_count",
                     "exact_rerun_cost_s_per_1000_ticks", "frontier_remaining", "exact_queue_remaining",
                     "wall_time_s")
    print(json.dumps({k: report[k] for k in summary_keys}, indent=2))
    return 0
