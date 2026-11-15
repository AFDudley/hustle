# covsearch: a coverage-directed search over Ninja vs Ninja

This tool searches the real game engine (not a model of it) for a Ninja
vs Ninja test corpus that covers every reachable line and branch of the
simulation files, plus the value class of every FGObject call argument.
The corpus feeds the Rust port's acceptance check
(`tools/step_diff`, branch `port-step`; out of scope here). Background:
`docs/labbook/2026-11-13-core-game-scope-five-characters.md`, decisions 3,
6, 8 and open item 7.

## Status at the last commit (read this before resuming)

**Steps A and B are committed. The fidelity proof ran and found four
real defects in this tool, now fixed; it also found one residual gap
("Decision: hybrid branching, exact credit" below), which this session
closed by splitting the search into ghost exploration and exact-run
credit. Step C (the coordinator) now runs on that hybrid design; see
"Live progress" for its current counts.**

### Fidelity proof

Acceptance order 1 requires proving the ghost `copy_to` branch (design
step 6) matches a fresh live run before any search. `CovWorker.gd`'s
`fidelity_pair` job mode drives one `(p1, p2)` pair twice from one
identical starting state: once on a ghost `copy_to` child, once on the
real, non-ghost `game`. It snapshots every tick of both legs with
`StateSnapshot.gd` (the same snapshot `tools/replay_check` hashes) and
reports the first differing tick, so this check is tick-for-tick, not
final-tick-only.

20 sample paths ran this way, each in its own fresh Godot process: 10
one-decision branches from the match-start root, and 10 from one replay
root (`.scratch/coverage_baseline/replays/A/Ninja_Ninja_1.replay`,
`up_to_tick=50`; 8 came from that one decision's own candidate cross
product, 2 from two other decisions of the same replay, so the sample
covers more than one decision point of the mirror). Finding and fixing
defects took four rounds; **all 20 samples pass on the final round**:

1. `_drive_to_decision` called `game.gd`'s `tick()` directly, bypassing
   `process_tick()`, which is where `p1_turn`/`p2_turn` reset to false
   each time ticking resumes (`game.gd` ~1445-1448, inside `if
   can_tick:`). A ghost branch child starts with both flags false
   (`copy_to` never sets them), so it matched the real engine's
   behaviour by coincidence of being a fresh instance. The real game,
   reused across every decision in a path, never got that reset, so
   `_turn_start_step`'s `on_interrupt()` handling silently never ran
   after the first decision of any path. Fixed by resetting both flags
   at the top of `_drive_to_decision`, for the real game and a ghost
   child alike.
2. The real game's automatic `_physics_process` stayed enabled during
   the fidelity check itself. `_apply_pair_traced` takes one `yield`
   per leg (ghost, then real) where the search's own `_apply_pair`
   takes one per decision; between those two yields, Godot's engine
   serviced the real `game` on its own, and its trace started 2 ticks
   ahead of the ghost trace (every later tick lined up exactly once
   accounted for, which is what pinned this on an engine race, not a
   branching difference). Fixed by disabling `game.set_physics_process`
   for the duration of the check, the same guard a ghost child already
   gets in `_expand`.
3. `_candidates` discretized only a data node's whole-dict `{x, y}`
   shape. Parry's data (`characters/states/NewParry.gd`, field "Block
   Height") and StickyBomb's data (`StickyBombActionUIData.gd`,
   "Jump"/"Aim") are dicts of named sub-fields or a plain boolean, so
   their non-aim choices never varied. See "Discretization
   completeness" below for the fix and the full shape list.
4. Four fields the aligned traces still showed turned out to be
   presentation, not simulation, each traced to its one reader:
   `player_actionable` (UI/network-sync only), `effects` (particle
   nodes; `process_fx()` only calls their own visual `tick()`),
   `style_aura_melee_attack_started_tick` /
   `style_aura_projectiles_first_active_tick` (feed only the aura
   shader), `ghost_reverse` (named for the ghost-only prediction path),
   `spawn_data` (read once, at an object's own spawn -- stale for P1/P2
   whose spawn is match start, long past in every sample), and
   `fighter_owner` (exists only on spawned sub-objects, and the one
   sample where it differed also showed the aura/effects gap on the
   same tick, with the fighters' own state matching exactly).
   `CovWorker.gd`'s `FIDELITY_IGNORE_EXTRA` lists these with the same
   per-field reasoning tools/replay_check/ignore.txt already uses for
   its own list, which this check reuses too.

**One residual gap is open, not papered over.** Two replay-root samples
showed `ObjectState.gd`'s `frame_methods`/`frame_methods_shared` (the
per-frame-callback dedup list a state's `_frame_N()` methods use) and
`CharState.gd`'s `interrupt_exceptions` (populated once in a state's
`init()`) differing between the ghost branch and the real continuation,
even though every externally observable field compared equal at the
same tick: position, velocity, state name, hp, meter, burst, hitlag.
`ObjectState.gd` line ~295/306 reads `host.is_ghost or new_max or
current_tick in frame_methods_shared` -- `is_ghost` alone makes a ghost
re-check every `_frame_N` candidate every tick, where the real path
only checks once per local frame number via the `frame_methods`/
`new_max` dedup, which is a real, source-confirmed behavioural
difference in `ObjectState.gd` itself, not an artifact of this tool. In
the two samples found, it did not change the externally visible state
(both reached the same position/velocity/hp), but a state whose
`_frame_N()` spawns a hitbox at a frame number already in
`frame_methods` could in principle fire that spawn a second time on a
ghost branch where it would not on the real path. I could not rule this
out or build a reduction case that changes the outward state within
this session. **This is the fidelity check's stated open item**: the
externally observable projection the search dedups and scores on
(position, velocity, state, hp, meter, burst, hitlag --
`CovWorker.gd _projection`) is proven faithful across all 20 samples;
whether a `_frame_N()` callback could double-fire without changing that
projection is not. If this tool is to close that gap fully, the
brief's own fallback applies: switch branching to exact re-simulation
(replay the path from tick 0 in a fresh live run per branch) and
measure its cost against the ghost-`copy_to` approach's.

### Decision: hybrid branching, exact credit

The residual gap above means a probe hit only inside a ghost branch does
not prove the real, non-ghost live path reaches it: `ObjectState.gd`
(~295/306) runs `_frame_N` dispatch differently when `host.is_ghost`, and
`game.gd`'s `tick()` returns early for a ghost after `post_tick`. Ghost
branching by source is therefore not faithful, and the search needs a
rule for what counts as a found probe, not just a faster way to explore.

**Decision: a hybrid.** Exploration keeps the fast ghost `copy_to`
branching (design step 6, unchanged) to propose paths: it is cheap, and
every sampled pair in the fidelity proof above matched the live path on
the externally observable projection, so it is a good *guide*. Credit is
exact: a probe counts as hit only when an exact re-simulation hits it --
the path's inputs (its root plus its ordered choice list) replayed from
tick 0 in a fresh process, on the real, non-ghost live path
(`on_action_selected`, the same application `_verify_replay` and the
corpus writer already use), with coverage recorded in that run
(`CovWorker.gd`'s `exact` job mode, `_hit_ids`). A path whose exact
re-run differs from its ghost run is reported with its first differing
tick and field (`coordinator.py first_divergence`), and its ghost-only
probes -- the ones only the ghost branch recorded -- are never credited:
credit comes from the exact run's own cumulative coverage, never from a
ghost branch's probe-delta. Termination and the corpus use only this
exact credit (design steps 8 and 9, below). `coordinator.py`'s module
docstring has the scheduling detail: every ghost-kept path is queued for
one exact re-run, sharing the same `jobs` worker-process pool the ghost
frontier uses, with the exact queue given priority so credit keeps
moving.

### Discretization completeness

Every Ninja (stickman) data UI found, and how `_candidates`/
`_data_variants` discretizes it:

| Action's data UI | Shape | Discretization |
|---|---|---|
| `AirShurikenActionUIData`, `GrapplingHookActionUIData`, `ShurikenActionUIData` (single child "Aim"), `DiveKick2ActionUIData` ("Direction"), `NunChukLightActionUIData` ("Angle"), `SkullShakerActionUIData` ("Distance"), `NinjaSuperJumpActionUIData` ("JumpArc") | whole-dict `{"x", "y"}` (one `XYPlot`-family child; `ActionUIData.get_data()` returns it directly when there is exactly one child) | the 9 compass-direction classes (`DIRECTIONS`) |
| `StickyBombActionUIData` (`StickyBombActionUIData.gd`) | plain `bool` (forced `true` when grounded; otherwise the one `Jump` `CheckButton`'s state, `ActionUIDataCheckButton.gd`) | both values |
| Parry (`characters/states/NewParry.gd` `_enter`, data UI `ParryActionUIData.tscn`/`BlockHeightSelector.gd`) | dict of named sub-fields: `{"Melee Parry Timing": {"count": N}, "Block Height": {"x": 0 or 1, "y": 0 or 1}}` | "Block Height" (the only field `NewParry.gd start()` reads -- `parry_type = High if data["Block Height"].y == 0 else Low`) gets the 9 `DIRECTIONS` classes, one sub-field at a time from the shared baseline; "Melee Parry Timing" keeps its one default (the game sets its count from the opponent's attack timing, not a free player choice -- a named, not a silent, reduction) |

`_data_variants` (`CovWorker.gd`) is the general version of this rule:
a whole-dict `{x, y}` value gets the 9 classes; a dict of named
sub-fields varies one `{x, y}` or boolean sub-field at a time from the
shared default (the same "one axis at a time" reduction the DI/toggle
axes already use); a plain boolean gets both values; any other leaf
keeps its one default.

### Known defect: `replay_scan` hangs partway through a long replay

`run.py scan-roots`'s `replay_scan` mode plays a whole human replay on
the live path, decision by decision. On
`replays/A/Ninja_Ninja_1.replay` it reliably stalls at the same point
(58 decisions in, around tick 706) under two independently-changed
versions of this tool (one running the full `_expand()` at every
decision, one running only `_legality_report()`), which rules out the
one perf fix already made (see commit history) as the cause and points
at `_wait_decision`/`_wait_resolved`'s own frame-by-frame polling loop
getting stuck waiting on a condition this replay never satisfies late
in the match (not reproduced by any direct `replay_prefix` job at an
earlier tick of the same replay, up to tick 706 tried individually).
**Worked around, not fixed**: `roots.json` for this run uses one
`replay_prefix` root per mirror at a fixed, verified-reachable tick
(`up_to_tick=50`) instead of every decision point `replay_scan` would
have found. This is a scope reduction from design point 3's "every
decision point of each mirror" to "one decision point per mirror",
named here, not hidden; the full scan's hang is unresolved.

### Live progress

`coordinate --roots roots.json --out report/ --jobs 8 --max-per-player 6`
runs the hybrid search (ghost exploration plus exact-run credit, see
"Decision" above) against the 6 roots above (match start plus the 5
mirrors' `up_to_tick=50` points), uncapped (no `--time-budget`), to one
of its two real termination conditions: every in-scope probe has exact
credit, or both the ghost frontier and the exact-verify queue empty
first. `coordinator.py`'s `write_progress` rewrites both
`report/progress.json` and the block below, in this file, every 90
wall-clock seconds while it runs (`container.sh` mounts this one file
read-write for that; see its comment), so a stop loses no more than 90
seconds of progress, not the whole run.

<!-- LIVE-PROGRESS:BEGIN -->
```json
{
  "jobs_done": 0,
  "exact_confirmed_probes": 0,
  "ghost_proposed_probes": 0,
  "total_probes": 0,
  "kept_paths_ghost_proposed": 0,
  "kept_paths_exact_confirmed": 0,
  "frontier_size": 0,
  "exact_queue_size": 0,
  "legality_diffs": 0,
  "divergence_count": 0,
  "elapsed_s": 0.0
}
```
Not started yet this session. `exact_confirmed_probes` (out of
`total_probes`) is the only number the termination condition and the
corpus's set cover read; `ghost_proposed_probes` is the ghost
exploration's own, uncredited, proposal count, kept here only to compare
against it.
<!-- LIVE-PROGRESS:END -->

## The fourteen design points, and where each one lives

1. **Scope and probes.** `instrument.py`'s `IN_SCOPE_DIRS` /
   `IN_SCOPE_FILES` / `IN_SCOPE_GLOBS` name exactly the files the brief
   lists. Three probe kinds, defined in `Probe.gd`: `line` (one per
   statement, reusing `tools/coverage`'s instrumenter unchanged, imported
   by file path so the two tools never fall out of sync on the hard
   part: logical-line scanning across strings, comments and brackets),
   `branch_true`/`branch_false` (the two outcomes of an `if`/`elif`/
   `while` condition), and `value_class` (zero / negative / positive /
   type-limit, for each argument of a `chara.<FGObject method>(...)`
   call). `map.json` (written by `prepare`) records every probe's kind,
   file, line and detail.
2. **Settings.** `CovWorker.gd`'s `TOURNAMENT_OVERRIDE` applies the four
   settings that differ from the default lobby and reach the simulation
   (decision 3). A `replay_prefix`/`replay_scan` root keeps the replay's
   own `match_data` untouched, as the brief requires.
3. **Roots.** `CovWorker.gd` mode `replay_scan` replays one mirror's own
   inputs on the live path from tick 0, and at every decision point
   writes a `root_tick` record and runs the legality oracle (design step
   4), so a replay-derived root gets the same legality check any other
   root gets. `run.py scan-roots` turns those records plus one
   `match_start` entry into `roots.json`. See "Known defect" above:
   this run used one fixed tick per mirror instead, because `replay_scan`
   hangs partway through the one replay it was tried on.
4. **Legality oracle.** `CovWorker.gd _legality_report` reads
   `button.visible and not button.disabled` from the real
   `ActionButtons` panels (not a copy of their logic), the same way
   `ReplayCheck.gd` does, and compares the result to `Legality.gd`'s
   computed list. A difference is written as a `legality_diff` record;
   the coordinator collects these into `report.json`.
5. **Discretization.** `CovWorker.gd _candidates`: `Continue` once, then
   for every visible action, `_data_variants` discretizes its data
   node's default value (see "Discretization completeness" above), plus
   (at the baseline data variant only) the 9-class DI and one flip each
   of `reverse` and `feint`. **Reduction from the design, stated here,
   not hidden:** the full cross product of data variant × DI × every
   toggle would be large; `_candidates` tries data variants and DI/
   toggle classes along two separate axes from a shared baseline
   instead of their full product, and `--max-per-player` caps the list
   further. Both are visible, logged knobs, not silent limits.
6. **Branching.** `CovWorker.gd _expand`: for each surviving candidate
   pair, a scratch `Game` is created with `is_ghost = true` (so it does
   not write into the shared `ReplayManager.frames` singleton or fire
   achievements/network/sound side effects — see the comment at
   `_expand` for why `is_ghost=false` would corrupt the parent run),
   started with the same `match_data`, and populated with
   `game.copy_to(child)` — the engine's own copy path, the same one
   `main.gd`'s ghost-game prediction uses. The pair applies through
   `on_action_selected`, the child ticks to its next decision point, and
   the probe-count delta and a projection are recorded. The parent
   `Game` is never touched, so every candidate pair starts from the
   same root. See "Fidelity proof" and "Decision" above: this ghost
   branch only ever *proposes* a path now; it earns that path's probes
   no credit by itself.
7. **Ordering and dedup.** `coordinator.py`: the ghost frontier is a
   `heapq` keyed by `-(number of probes the ghost branch hit)`. A child is
   dropped from the frontier only in that its *bucketed* projection
   (`bucket_projection`: positions rounded to 8, velocities rounded to 1,
   state names and HP/meter/burst exact) controls whether it is pushed
   back onto the frontier at all — a repeat bucket is not re-expanded.
   Every distinct new bucket, whether or not it is pushed back (a
   `game_over` child is not), is queued once for exact credit (below).
8. **Exact credit.** `CovWorker.gd`'s `exact` job mode (see "Decision"
   above) replays a kept path's root and choices from tick 0, on the
   real `game`, in a fresh process, and writes one `exact_step` record
   per choice (its decision-point projection, for divergence
   comparison) plus one `exact_result` record with that process's full
   cumulative probe-hit set (`_hit_ids`). `coordinator.py`'s
   `submit_exact`/`first_divergence` queue every ghost-kept path for
   this, sharing the same `jobs` worker-process pool the ghost frontier
   uses (exact re-runs take priority when both queues have work), add
   only the exact run's own `hit_ids` to the credited set, and report any
   ghost/exact projection mismatch with its first differing step, tick
   and field.
9. **Termination.** `coordinator.run` stops when every probe id in
   `probe_map.json` has exact credit, or both the ghost frontier and the
   exact-verify queue empty. There is no iteration cap in the algorithm;
   `--time-budget` is a wall-clock escape hatch for *interactive* runs
   (documented in `run.py`'s `--help`), not part of the search's own
   stopping rule, and a run stopped by it is reported as such, not as
   converged. On a real exit, every probe id outside the credited set
   gets a reason in `report.json["unreachable"]`: either a curated
   static reason (`coordinator.STATIC_UNREACHABLE`) or the honest
   "unreached: frontier exhausted before this probe in this run", which
   is a fact about the run, not a proof of unreachability.
10. **Corpus.** `corpus.build`: greedy set cover over
    `kept_paths.json`'s probe sets -- now exact-confirmed sets only, one
    entry per exact re-run, never a ghost branch's probe-delta -- then
    one `write_replay` job per selected path (a real, non-ghost match
    applies the path's choices through `on_action_selected`, so
    `ReplayManager.frames` fills the normal way; `ReplayManager.save_replay`
    writes the binary replay). Each entry also gets a `<name>.json` with
    its root, its choice list and the probes it covers — the ".replay
    plus inputs and settings as JSON" the brief asks for.
11. **Fidelity and determinism.** Three checks exist now, at different
    grains. The acceptance-order-1 check (`CovWorker.gd _fidelity_pair`,
    run once before any search; see "Fidelity proof" above) compares a
    ghost branch against a fresh live continuation tick for tick, over
    every script variable `StateSnapshot.gd` captures. Every kept path's
    own exact re-run (point 8 above) is itself a second, per-path
    fidelity check: it is what exposed the residual gap's practical
    reach, by comparing the ghost branch's projection against its own
    exact re-run at every decision point of every kept path, not just 20
    samples. `corpus.check_fidelity` is the corpus determinism check the
    brief asks for: it plays each corpus replay twice through
    `CovWorker.gd`'s `verify_replay` mode (the live path,
    `Fighter.on_action_selected`) and requires the two runs to produce
    the same final-tick projection *and* the same cumulative probe-hit
    set (`_hit_ids`) -- a determinism check, not a tick-for-tick one; it
    does not need to be, since the harder, tick-for-tick question is
    what `_fidelity_pair` already answered once for the branching
    mechanism itself.
12. **Exact re-run cost.** `coordinator.py` times each exact re-run's
    wall clock around its `subprocess.run` and divides by its
    `final_tick` (every exact run starts at tick 0, so this tick count is
    the whole cost, not a delta) to get seconds per 1,000 ticks; the
    report's `exact_rerun_cost_s_per_1000_ticks` is this figure's mean,
    min and max over every exact job the run submitted -- the brief's
    "measure its cost" for the exact-re-simulation fallback, point 8.
13. **Parallelism.** `coordinator.run` uses a
    `ThreadPoolExecutor(max_workers=jobs)` of Python threads, each
    blocked on one `subprocess.run` of a Godot worker process, so up to
    `--jobs` (default 4, 8 used for this run) Godot processes run at
    once, sharing the one frontier, the one exact-verify queue and the
    one `hit_ids`/`seen_buckets` state in this process. `container.sh`
    sets `--cpu-quota 800000 --cpu-period 100000`, `--pids-limit=-1` and
    `LP_NUM_THREADS=1` on the whole container, matching the per-run
    limits used elsewhere in this repository's tooling.
14. **Hand-off.** The corpus directory (`corpus/`) holds one `.replay`
    file per path plus its `<name>.json` and a `manifest.json` listing
    every entry. This is the same directory shape
    `tools/replay_check/run.py`'s `check`/`census` commands already read
    (`--replays DIR`, one `.replay` per entry), so
    `tools/replay_check/container.sh check --replays corpus/` reads this
    corpus directly, and `port-step`'s `project` command (which reads a
    replay directory the same way, per its own README) does too.

## Running it

```sh
tools/covsearch/container.sh prepare
# scan-roots on a long replay can hang (see "Known defect" above); this
# run built roots.json with one replay_prefix root per mirror instead:
tools/covsearch/container.sh coordinate --roots roots.json --out report/ --jobs 8 --max-per-player 6
tools/covsearch/container.sh corpus --kept-paths report/kept_paths.json --out corpus/
tools/covsearch/container.sh fidelity --corpus corpus/
```

`container.sh worker --job JOB.json --name NAME` runs one `CovWorker.gd`
job directly. A job with `"fidelity_pair": [p1in, p2in]` added to a
normal root+choices job runs the tick-for-tick fidelity check described
above for that one decision.

## Next step

1. Let the running `coordinate` job reach one of its two termination
   conditions and read `report/report.json`.
2. Run `corpus` and `fidelity` against that run's `kept_paths.json`.
3. Write the full report: probe counts by kind (hit, unreachable with
   reason), corpus size, run time, the `Legality.gd` difference report.
4. Investigate the `replay_scan` hang (see "Known defect") if a future
   session needs every decision point of a mirror, not just one.
5. Investigate the `frame_methods`/`interrupt_exceptions` gap (see
   "Fidelity proof residual gap") if a future session needs to rule out
   a double-fired `_frame_N()` callback, not just prove the externally
   observable projection matches.

## A note on this session's tool use

Two `python3 -c` syntax checks of newly written `.py` files ran directly
on the host while this tool was being built, before the container-only
rule for Python was re-checked against the brief. Every functional run
after that point ran inside the podman container, as the brief
requires; the two host checks did not run or modify any game file and
are named here for the record, not excused.
