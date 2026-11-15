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

Parallel frontier (this module's own scheduling, not a CovWorker.gd
change): a `list_candidates` job for one frontier node does not itself
produce a branch, so running exactly one frontier node at a time left the
`--jobs` worker pool idle between a node's own `list_candidates` job and
its `branch_batch` fan-out whenever that node had fewer candidate pairs
than `--jobs`. `run()` instead keeps a standing backlog of submitted
jobs -- several nodes' `list_candidates` jobs and several nodes'
`branch_batch` jobs, from different frontier nodes, in flight on the one
shared `ThreadPoolExecutor(max_workers=jobs)` at once -- topped up every
time any job completes, so a freed worker process always has queued work
instead of waiting for one node's own two-phase expansion to finish.

Ordering (step 7): the frontier is a priority queue; a node's priority is
the number of probes its branch hit that no kept path has hit yet ("new
probes reached"). Dedup (step 7): a child is dropped when its bucketed
projection (positions and velocities bucketed, state names and tick kept
exact) was already seen.

Termination (step 8): the run stops when every in-scope probe id (from
probe_map.json) has been hit, or the frontier and the in-flight job
backlog both empty first (`frontier_exhausted`) -- no time budget and no
iteration cap end the search early; those are the only two reasons
`report.json["termination_reason"]` ever names. On a `frontier_exhausted`
exit, every unhit probe gets a reason in the report: either a reason this
module can prove from the probe's own source line (`classify_unreachable`
below -- a ghost-only, network-only, spectator-only or replay/undo-only
guard this live, singleplayer, non-ghost search can never satisfy), or,
lacking a provable reason, the literal string "UNEXPLAINED". Any
UNEXPLAINED probe makes `run()` return 1: the brief's own rule is that an
unproven gap fails the run rather than hiding behind a vague default.

Resumable run: `run()` checkpoints its full state -- the frontier, every
counter, `hit_ids`, `seen_buckets`, `kept_paths`, `legality_diffs` and
`stalled_nodes` -- to `<out>/checkpoint.json` every
`CHECKPOINT_INTERVAL_S` seconds (written to a temp file and renamed, so a
kill mid-write never leaves a half-written checkpoint). The next
`coordinate` invocation against the same `--out` directory loads this
checkpoint automatically (ignoring `--roots`, which only seeds a fresh
run) and continues the same search; a run that already finished resumes
into an empty frontier and empty in-flight backlog and just re-emits the
same final report, a safe no-op.
"""

from __future__ import annotations

import concurrent.futures
import heapq
import itertools
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

Bucket = tuple

CHECKPOINT_INTERVAL_S = 120.0


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


def _bucket_to_list(b: Bucket) -> list:
    game_over, items = b
    return [game_over, [list(item) for item in items]]


def _bucket_from_list(lst: list) -> Bucket:
    game_over, items = lst
    return (game_over, tuple(tuple(item) for item in items))


def load_probe_map(work_root: Path) -> dict[int, dict]:
    raw = json.loads((work_root / "probe_map.json").read_text())
    probes: dict[int, dict] = {}
    for res_path, records in raw["files"].items():
        for rec in records:
            probes[rec["id"]] = {**rec, "file": res_path}
    return probes


# --- static unreachable classification (design step 8) -------------------
#
# Every name below is a fact about THIS TOOL's own driving code, true for
# every root this search ever builds, not a guess about one replay's data:
#   - is_ghost: CovWorker.gd's `_new_game` never sets a Game's or a fighter's
#     `is_ghost` true; every Game this tool builds is the one, real,
#     non-ghost Game (header comment, "Operator decision: no ghost
#     branching").
#   - ReplayManager.playback: every `_build_root_and_choices`,
#     `_verify_replay` and `_scan_replay` call sets this false before
#     building a Game; the live path never flips it, by design (design
#     step 6: "ReplayManager.playback = false, the same mechanism
#     tools/replay_check's live mode uses").
#   - ReplayManager.resimulating / replaying_ingame: only the undo/rewind
#     UI path sets these; this tool never calls `undo()`.
#   - Network.multiplayer_active, SteamLobby.SPECTATING: this tool never
#     starts a network session or a spectated session; every match is
#     `match_data["singleplayer"] = true` (match_start) or a replay's own
#     recorded singleplayer practice session (replay_prefix/replay_scan).
# Confirmed empirically this session, not just by reading the code: a
# fresh tools/replay_check "live" run of this search's own
# Ninja_Ninja_1.replay root unlocked ACH_CHESS, which `end_game()` only
# does when is_ghost, ReplayManager.playback/replaying_ingame/is_in_replay,
# Network.multiplayer_active and SteamLobby.SPECTATING are ALL false --
# the same facts this classifier relies on.
#
# A TOURNAMENT_OVERRIDE setting (stage_width, game_length, ceiling_height,
# starting_meter) is deliberately NOT in this list: `.scratch/settings/
# settings.jsonl` shows this run's own replay_prefix roots keep each
# replay's own recorded values (`stage_width: 800`, `game_length: 3600`
# for the "B" mirrors used here), not the tournament override the
# match_start root applies -- a setting-dependent branch is reachable
# through whichever root actually carries the value that makes it true,
# so no single fixed value is safe to assert across every root in THIS
# run's roots.json. Asserting one anyway would be inventing a reason the
# code does not prove; left for a human to add per-root if a future
# roots.json needs it.
KNOWN_FALSE_BARE = (
    "is_ghost",
    "spectating",
)
KNOWN_FALSE_EXACT = (
    "ReplayManager.playback",
    "ReplayManager.resimulating",
    "ReplayManager.replaying_ingame",
    "Network.multiplayer_active",
    "SteamLobby.SPECTATING",
)


def _atom_known_false(atom: str) -> bool:
    a = atom.strip()
    for bare in KNOWN_FALSE_BARE:
        if a == bare or a.endswith("." + bare):
            return True
    return a in KNOWN_FALSE_EXACT


def _term_truth(term: str) -> str | None:
    """'false'/'true' when `term` (one and/or operand, possibly negated) is
    provably that value from the facts above; None when it is not provable
    either way."""
    t = term.strip()
    if t.startswith("!"):
        return "true" if _atom_known_false(t[1:].strip()) else None
    if t.startswith("not "):
        return "true" if _atom_known_false(t[4:].strip()) else None
    return "false" if _atom_known_false(t) else None


def _split_top(expr: str, keyword: str) -> list[str]:
    """Split `expr` on top-level ` keyword ` tokens (keyword "and"/"or"),
    tracking paren depth per whitespace token so a balanced function call's
    own parentheses (for example `current_state().push`) never count as a
    grouping boundary. Only meant for the simple, space-separated
    conditions this codebase actually writes; see `_condition_truth`."""
    parts = []
    depth = 0
    cur: list[str] = []
    for tok in expr.split(" "):
        depth += tok.count("(") - tok.count(")")
        if depth == 0 and tok == keyword:
            parts.append(" ".join(cur))
            cur = []
        else:
            cur.append(tok)
    parts.append(" ".join(cur))
    return parts


def _and_group_truth(group: str) -> str | None:
    truths = [_term_truth(t) for t in _split_top(group, "and")]
    if any(t == "false" for t in truths):
        return "false"
    if truths and all(t == "true" for t in truths):
        return "true"
    return None


def _condition_truth(cond: str) -> str | None:
    """'false'/'true' when `cond` (an if/elif/while header's own condition
    text) is provably always that value from the known-false facts above;
    None when the condition mixes provable and unprovable terms, or uses
    explicit grouping parentheses this splitter does not resolve -- in
    which case the caller leaves the probe unclassified rather than guess.
    """
    if cond.count("(") != cond.count(")"):
        return None
    truths = [_and_group_truth(g) for g in _split_top(cond, "or")]
    if all(t == "false" for t in truths):
        return "false"
    if any(t == "true" for t in truths):
        return "true"
    return None


COND_HEADER_RE = re.compile(r"^(if|elif|while)\s+(.*):\s*$")


def classify_unreachable(probe: dict, repo_root: Path) -> str | None:
    """A code-derived reason for `probe` being unreachable in this search,
    or None if this module cannot prove one. Reads the real, uninstrumented
    source line at the probe's own file and line number (never the
    instrumented copy, so a `__COV__.b(...)` wrapper is never mistaken for
    the original condition) and checks only the narrow, provable patterns
    documented above `KNOWN_FALSE_BARE`. Never invents a reason: anything
    this function cannot prove returns None, and the caller reports that
    probe as UNEXPLAINED."""
    file_field = probe["file"]
    rel = file_field[len("res://"):] if file_field.startswith("res://") else file_field
    path = repo_root / rel
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    line_no = probe["line"]
    if not (1 <= line_no <= len(lines)):
        return None
    stripped = lines[line_no - 1].strip()
    if probe["kind"] not in ("branch_true", "branch_false"):
        return None
    m = COND_HEADER_RE.match(stripped)
    if not m:
        return None
    cond = m.group(2).strip()
    truth = _condition_truth(cond)
    if truth == "false" and probe["kind"] == "branch_true":
        return (
            f"unreachable: `{cond}` ({file_field}:{line_no}) is always false in a live, "
            "singleplayer, non-ghost, non-replaying search (is_ghost, ReplayManager."
            "playback/resimulating/replaying_ingame, Network.multiplayer_active and "
            "SteamLobby.SPECTATING are never true here)"
        )
    if truth == "true" and probe["kind"] == "branch_false":
        return (
            f"unreachable: `{cond}` ({file_field}:{line_no}) is always true in a live, "
            "singleplayer, non-ghost, non-replaying search, by the same facts"
        )
    return None


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


def _is_placeholder(c: dict) -> bool:
    return c.get("action") == "Continue" and c.get("extra") is None


def run(
    work,
    roots_path: Path,
    out_dir: Path,
    jobs: int,
    run_worker: Callable[[Any, str, dict], Path],
    max_per_player: int = 12,
    readme_path: Path | None = None,
    repo_root: Path | None = None,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    probes = load_probe_map(work.root)
    total_ids = set(probes.keys())

    checkpoint_path = out_dir / "checkpoint.json"
    progress_path = out_dir / "progress.json"

    hit_ids: set[int] = set()
    seen_buckets: set[Bucket] = set()
    legality_diffs: list[dict] = []
    kept_paths: list[dict] = []  # {"root": ..., "choices": [...], "probes": [...]}
    total_branches = 0
    total_ticks_simulated = 0  # sum of every branch's own final tick
    branch_job_seconds: list[float] = []  # one entry per branch_batch job
    branch_job_pairs: list[int] = []
    stalled_nodes: list[dict] = []  # a worker job stalled past its own timeout
    jobs_done = 0
    elapsed_before = 0.0
    frontier: list[tuple[int, int, dict]] = []
    seq_box = [0]

    def next_seq() -> int:
        v = seq_box[0]
        seq_box[0] += 1
        return v

    if checkpoint_path.exists():
        state = json.loads(checkpoint_path.read_text())
        hit_ids = set(state["hit_ids"])
        seen_buckets = {_bucket_from_list(b) for b in state["seen_buckets"]}
        legality_diffs = state["legality_diffs"]
        kept_paths = state["kept_paths"]
        total_branches = state["total_branches"]
        total_ticks_simulated = state["total_ticks_simulated"]
        branch_job_seconds = state["branch_job_seconds"]
        branch_job_pairs = state["branch_job_pairs"]
        stalled_nodes = state["stalled_nodes"]
        jobs_done = state["jobs_done"]
        elapsed_before = state["elapsed_s"]
        seq_box[0] = state["next_seq"]
        for neg_score, seq, node in state["frontier"]:
            heapq.heappush(frontier, (neg_score, seq, node))
        print(
            f"resumed {checkpoint_path}: {len(hit_ids)}/{len(total_ids)} probes hit, "
            f"{len(frontier)} frontier nodes, {elapsed_before:.0f}s already elapsed",
            file=sys.stderr,
        )
    else:
        roots = json.loads(roots_path.read_text())
        for i, root in enumerate(roots):
            heapq.heappush(frontier, (-(10**9), next_seq(), {"root": root, "choices": []}))

    start = time.monotonic()
    last_checkpoint = time.monotonic()
    last_readme_write = 0.0
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)
    in_flight: dict[concurrent.futures.Future, tuple] = {}
    # Keep this many jobs submitted (not merely running) at once: more than
    # `jobs` so a freed worker always has queued work from SOME frontier
    # node, instead of idling between one node's own list_candidates job
    # and its branch_batch fan-out (see module docstring, "Parallel
    # frontier").
    backlog_target = jobs * 2

    def elapsed() -> float:
        return elapsed_before + (time.monotonic() - start)

    def all_hit() -> bool:
        return bool(total_ids) and total_ids <= hit_ids

    def write_progress():
        nonlocal last_readme_write
        e = elapsed()
        progress = {
            "jobs_done": jobs_done,
            "hit_count": len(hit_ids),
            "total_probes": len(total_ids),
            "kept_paths": len(kept_paths),
            "frontier_size": len(frontier),
            "in_flight": len(in_flight),
            "legality_diffs": len(legality_diffs),
            "stalled_nodes": len(stalled_nodes),
            "branches_recorded": total_branches,
            "ticks_simulated": total_ticks_simulated,
            "branches_per_second": total_branches / e if e > 0 else 0.0,
            "ticks_per_second": total_ticks_simulated / e if e > 0 else 0.0,
        }
        progress_path.write_text(json.dumps(progress, indent=2))
        now = time.monotonic()
        if readme_path is not None and now - last_readme_write >= 90.0:
            _update_readme_progress(readme_path, progress)
            last_readme_write = now

    def save_checkpoint():
        state = {
            "frontier": [[ns, sq, nd] for ns, sq, nd in frontier],
            "next_seq": seq_box[0],
            "hit_ids": sorted(hit_ids),
            "seen_buckets": [_bucket_to_list(b) for b in seen_buckets],
            "legality_diffs": legality_diffs,
            "kept_paths": kept_paths,
            "stalled_nodes": stalled_nodes,
            "total_branches": total_branches,
            "total_ticks_simulated": total_ticks_simulated,
            "branch_job_seconds": branch_job_seconds,
            "branch_job_pairs": branch_job_pairs,
            "jobs_done": jobs_done,
            "elapsed_s": elapsed(),
        }
        tmp = checkpoint_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(checkpoint_path)

    def submit_list(node: dict):
        name = f"list{next_seq()}"
        job = {
            "base": node["root"], "choices": node["choices"],
            "action": "list_candidates", "max_per_player": max_per_player,
        }
        fut = executor.submit(run_worker, work, name, job)
        in_flight[fut] = ("list", node)

    def submit_branch(node: dict, batch: list):
        name = f"branch{next_seq()}"
        job = {
            "base": node["root"], "choices": node["choices"],
            "action": "branch_batch", "pairs": batch,
        }
        submit_t0 = time.monotonic()
        fut = executor.submit(run_worker, work, name, job)
        in_flight[fut] = ("branch", node, batch, submit_t0)

    def refill():
        while len(in_flight) < backlog_target and frontier and not all_hit():
            _, _, node = heapq.heappop(frontier)
            submit_list(node)

    termination_reason = "frontier_exhausted"
    refill()
    while in_flight:
        if all_hit():
            termination_reason = "all_probes_hit"
            break
        done, _ = concurrent.futures.wait(list(in_flight), return_when=concurrent.futures.FIRST_COMPLETED)
        for fut in done:
            entry = in_flight.pop(fut)
            if entry[0] == "list":
                _, node = entry
                try:
                    list_out = fut.result()
                except Exception as exc:
                    if type(exc).__name__ != "WorkerStalled":
                        raise
                    stalled_nodes.append({"node": node, "phase": "list_candidates", "reason": str(exc)})
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
                # A player with no real options gets one synthetic
                # placeholder entry from CovWorker.gd _candidates. If both
                # players are in that state, this node is not a real
                # decision; CovWorker.gd _apply_pair_live already refuses
                # to send that placeholder as an input, so branching it
                # would add nothing.
                both_placeholder = (
                    all(_is_placeholder(p1) for p1 in p1_candidates)
                    and all(_is_placeholder(p2) for p2 in p2_candidates)
                )
                if pairs and not both_placeholder:
                    for batch in _chunks(pairs, jobs):
                        submit_branch(node, batch)
            else:  # "branch"
                _, node, batch, submit_t0 = entry
                try:
                    out_path = fut.result()
                except Exception as exc:
                    if type(exc).__name__ != "WorkerStalled":
                        raise
                    stalled_nodes.append({"node": node, "phase": "branch_batch", "pairs": batch, "reason": str(exc)})
                    continue
                branch_job_seconds.append(time.monotonic() - submit_t0)
                branch_job_pairs.append(len(batch))
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
                        heapq.heappush(frontier, (-score, next_seq(), child))
                    if is_new_state:
                        kept_paths.append({**child, "probes": rec["probes"]})
        refill()
        write_progress()
        if time.monotonic() - last_checkpoint >= CHECKPOINT_INTERVAL_S:
            save_checkpoint()
            last_checkpoint = time.monotonic()

    if all_hit():
        termination_reason = "all_probes_hit"
    elif not frontier and not in_flight:
        termination_reason = "frontier_exhausted"

    write_progress()
    save_checkpoint()
    executor.shutdown(wait=False, cancel_futures=True)

    missing = total_ids - hit_ids
    unreachable: dict[int, str] = {}
    unexplained: list[int] = []
    for pid in sorted(missing):
        reason = classify_unreachable(probes[pid], repo_root) if repo_root is not None else None
        if reason is None:
            unexplained.append(pid)
            unreachable[pid] = "UNEXPLAINED"
        else:
            unreachable[pid] = reason

    elapsed_s = elapsed()
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
        "unexplained_count": len(unexplained),
        "unreachable": {str(pid): {"reason": unreachable[pid], **probes[pid]} for pid in sorted(missing)},
        "legality_diffs": legality_diffs,
        "stalled_nodes": stalled_nodes,
        "kept_paths": len(kept_paths),
        "frontier_remaining": len(frontier),
        "branches_recorded": total_branches,
        "ticks_simulated": total_ticks_simulated,
        "branches_per_second": total_branches / elapsed_s if elapsed_s > 0 else 0.0,
        "ticks_per_second": total_ticks_simulated / elapsed_s if elapsed_s > 0 else 0.0,
        "branch_batch_jobs": len(branch_job_seconds),
        "seconds_per_branch_batch_job": seconds_per_branch_job,
        "seconds_per_branch": seconds_per_branch,
        "wall_time_s": elapsed_s,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "kept_paths.json").write_text(json.dumps(kept_paths, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k not in ("unreachable", "legality_diffs", "stalled_nodes")}, indent=2))
    if unexplained:
        print(
            f"FAIL: {len(unexplained)} unhit probes have no code-derived reason (UNEXPLAINED); "
            f"see report.json[\"unreachable\"] for their ids",
            file=sys.stderr,
        )
        return 1
    return 0
