# Free coverage: what random Ninja vs Ninja play covers

This report measures how much of the Ninja-vs-Ninja simulation code random
play covers, under the TWIN RULE (operator decision): an instrumented run's
coverage counts only when the same inputs, replayed on the plain,
uninstrumented project, produce an identical per-tick trace for the whole
match. A mismatch would be reported with its first differing tick and field
and would not count. This measurement is the input to the next decision
named in the task: trace the remaining gap by hand from source, or fuzz for
it.

Raw data sits beside this file in `.scratch/free_coverage_report/`:
`summary.json` (twin results and the final hit-probe id set),
`matches.jsonl` (one record per match), `kind_breakdown.tsv`,
`coverage_curve.json`, `classified_unreachable_probes.json`, and
`unhit_by_file_function.tsv`.

## Tool

`tools/covsearch/free_coverage.py` (new), wired into `tools/covsearch/run.py`
as the `free-coverage` command. For each match:

1. **Generate** (skipped for the 5 mirrors, which already have a replay):
   `tools/replay_check`'s own `generate_job`, tournament settings
   (`stage_width=600, game_length=3000, ceiling_height=500,
   starting_meter="1.5"`, lab book decision 3), Ninja vs Ninja, a seeded
   RNG, run on the **plain** project.
2. **Live, plain**: the same replay's recorded inputs, applied through
   `Fighter.on_action_selected` (`tools/replay_check`'s "live" mode), on
   the plain project. Writes a per-tick hash trace.
3. **Live, instrumented**: the same replay, same mechanism, on the project
   instrumented by `tools/covsearch/instrument.py` (line, branch, and
   value-class probes; `Probe.gd` writes the hit counts via
   `COVERAGE_OUT`). Writes its own per-tick hash trace.
4. **Twin check**: `tools/replay_check/run.py`'s `compare_traces`, with
   `ignore.txt`, on the two traces. A match with zero divergence episodes
   and no tick-sequence/length problem credits its probe hits; any other
   match does not.

Up to 8 Godot processes run at once (`ThreadPoolExecutor(max_workers=jobs)`,
`container.sh`'s own `--cpu-quota 800000 --cpu-period 100000
--pids-limit=-1`, `LP_NUM_THREADS=1`). The command resumes from a prior
`matches.jsonl` by name, so a second invocation against the same `--out`
tops up without re-running an already-completed match.

### A fix made while building this

`instrument.py`'s `IN_SCOPE_FILES` named `characters/ObjectState.gd` and
`characters/CharState.gd`, which have never existed: `ObjectState.gd` lives
at the repository root and `CharState.gd` lives under
`characters/states/`. Both typos predate this session. The effect:
`ObjectState.gd` -- the shared per-object state-machine base every
character's state extends -- was never instrumented by any prior
`tools/covsearch` run; `CharState.gd` was still covered through
`IN_SCOPE_GLOBS`'s `characters/states/*.gd`, so only `ObjectState.gd` was
silently missing. Fixed in this session's `instrument.py`; this run is the
first to measure `ObjectState.gd` at all.

## The run

300 random Ninja vs Ninja matches (seeds 1-300) plus the 5 Ninja mirrors,
requested. Two container invocations were needed:

| Invocation | What happened |
|---|---|
| 1st | Task order queued the 5 mirrors behind all 300 random seeds in the one shared thread pool, so the 7200s time budget (which the brief allows when 300 takes too long) expired before any mirror started: 169 random matches attempted, 0 mirrors. Found from the first run's own `matches.jsonl` having zero `mirror*` entries. Wall time 7535.4s (~2h 5min). |
| Fix | `free_coverage.py`'s `run()` now submits the 5 mirrors first, and resumes from the existing `matches.jsonl` by name instead of re-running finished work. |
| 2nd (resume) | Mirrors completed first (each 64-280s), then random seeds continued from 171. Bash's own tool timeout (1500s) cut this invocation off at seed 199, inside its own 1200s `--time-budget`; the host-side summary was then rebuilt from `matches.jsonl` directly (`jq`, no re-run). |

**Total wall time across both invocations: ~9040s (about 2h 31m).** This
exceeds the brief's "about 2 hours" target because of the mirror-ordering
defect above, which cost most of the first invocation's budget without
producing mirror data; the fix landed before any further matches ran.

**Matches attempted: 204 of the requested 305** (199 random seeds, 1-199,
plus all 5 mirrors). Random-match rate with 8-way parallelism: about 230s
per match on average (`sum(wall_s)/204 ≈ 321s` per match including queue
wait; `65475.6s` total recorded worker time / 204 matches), i.e. roughly
1.6 matches/min of wall-clock throughput at 8 workers. At that rate, the
full 305 would need about another 50 minutes beyond what ran.

## Twin results

| | |
|---|---|
| Matches attempted | 204 (199 random seeds 1-199, 5 mirrors) |
| Twin pass | 199 |
| Twin fail | 5 |
| Divergence episodes among twin-pass matches | **0** (every passing match's plain and instrumented traces were tick-for-tick identical, confirmed over all 199+5 matches, not just a sample) |

**All 5 twin failures are not twin mismatches.** Each failed at the
**generate** step, on the plain project, before any trace comparison ran:

```
SCRIPT ERROR: Invalid get index 'id' (on base: 'Nil'). at: hit_by
(res://characters/stickman/projectiles/Caltrops.gd:18)
```

Seeds 3, 52, 63, 68, 142. This is a real defect in the committed game's
Caltrops projectile (a Ninja move): `hit_by` reads `.id` off something that
is `Nil` for a subset of random inputs. It reproduces on the **plain**
project with no instrumentation involved, so it is a genuine gameplay bug,
not a tool defect. Filing it as a bug is outside this task's scope (the
brief asks only for measurement); it is named here as a finding. No replay
or trace exists for these 5 seeds, so they contribute no probe hits and no
twin mismatch either, by construction -- they never reached the comparison
step.

## Probe counts by kind

| Kind | Total | Hit | Unhit |
|---|---:|---:|---:|
| line | 6,141 | 4,437 | 1,704 |
| branch_true | 1,571 | 976 | 595 |
| branch_false | 1,571 | 1,014 | 557 |
| value_class | 156 | 73 | 83 |
| **Total** | **9,439** | **6,500 (68.9%)** | **2,939 (31.1%)** |

## Coverage curve

Cumulative distinct probes hit after the Nth random match (seed order,
twin-pass matches only; the 5 mirrors are not part of this curve, since
they are fixed inputs, not a growing random sample):

| Matches | Probes hit |
|---:|---:|
| 10 | 5,816 |
| 30 | 5,960 |
| 100 | 6,032 |
| 199 (all random matches run) | 6,058 |

The curve is already nearly flat by 10 matches (5,816 of the eventual
6,058, with the 5 mirrors) and gains only 242 more probes over the next
189 matches. Random tournament-settings Ninja vs Ninja play saturates its
own reachable code quickly; the remaining gap is not a sampling-size
problem.

## Unhit probes: the big, verified buckets

2,939 probes are unhit. 68 of them have a reason **proved from source**,
reusing `tools/covsearch/coordinator.py`'s own `classify_unreachable`
(unchanged, run against every unhit probe's real, uninstrumented source
line): a branch whose condition reads `is_ghost`,
`ReplayManager.playback`/`resimulating`/`replaying_ingame`,
`Network.multiplayer_active`, or `SteamLobby.SPECTATING` is always false in
this live, singleplayer, non-ghost, non-replaying driving mechanism, so one
of its two outcomes never fires. These 68 spread across `game.gd` (39),
`characters/BaseChar.gd` (13), `obj/BaseObj.gd` (5), `mechanics/Hitbox.gd`
(4), `ObjectState.gd` (3), `characters/states/Frozen.gd` (2),
`characters/states/Knockdown.gd` (1), `characters/states/Forfeit.gd` (1).

Beyond that narrow, line-level proof, source reading this session found
three **whole-function or whole-file** reasons that are not Ninja-specific
game rules but facts about either this tool's own driving mechanism or
about which character owns a mechanic:

**1. The ghost-prediction `copy_to` family -- unreachable by this tool's
own construction, not a Ninja limitation.** `game.gd copy_to` is called
only from `main.gd _start_ghost` (confirmed by grep: no other caller
anywhere in the repository). `main.gd` is the real UI flow's main scene;
neither `tools/replay_check/ReplayCheck.gd` nor `tools/covsearch/
CovWorker.gd` ever instantiates it -- both build a bare `Game` node
directly (`tools/replay_check/README.md`'s own "Limits" section already
names this: "`generate` runs no prediction ghost game"). So `game.gd
copy_to` (29 unhit/29 total), `characters/BaseChar.gd copy_to` (14/14),
`obj/BaseObj.gd copy_to` (66/66) and `_copy_state_variables_to` (7/7),
`ObjectState.gd copy_to`/`_copy_to`/`copy_data`/`copy_hurtbox_states`
(3+10+10+11, all 100%), `mechanics/Hitbox.gd copy_to` (6/6),
`obj/ObjHooks.gd copy_to` (1/1), and `game.gd ghost_tick` (116/116, also
only called from code gated on the ghost-prediction loop) are unreachable
by every tool built on this driving mechanism, `tools/covsearch` included,
not specifically by Ninja vs Ninja. This one family accounts for **263 of
the 2,939 unhit probes**.

**2. `mechanics/SweptHitbox.gd` -- the entire file belongs to other
characters.** Confirmed by grep: no script anywhere `extends SweptHitbox`
except the class itself; the only three scenes that attach it are
`characters/mutant/projectiles/CausticThorn.tscn` (Mutant),
`characters/swordandgun/projectiles/TemporalPath.tscn` (Cowboy), and
`characters/wizard/Wizard.tscn` (Wizard). Ninja never spawns a swept
(continuous-collision) hitbox. `mechanics/SweptHitbox.gd` sits in an
in-scope shared directory (`mechanics/`), so `instrument.py` probed it, but
every one of its 21 functions is 100% unhit: **~400 of the 2,939 unhit
probes** (the file's full probe count; every function in it is in the
"fully unhit" list).

**3. `obj/BaseObj.gd`'s `randi_choice`/`randi_percent`/
`randi_weighted_choice`/`hash_rng` family -- other characters' randomized
attacks.** Confirmed by grep: `host.randi_choice`/`randi_percent`/
`randi_weighted_choice` are called from `characters/wizard/states/
ConjurePebble.gd` (Wizard's randomized-projectile move); no Ninja state
calls any of this family. Ninja's own moveset has no randomized-choice
attack, so these base-class helpers (declared in the shared `obj/BaseObj.gd`
for any character to use) never run in Ninja vs Ninja. ~24 probes.

**4. `characters/states/Frozen.gd` -- a shared state gated on a hitbox
property no Ninja move ever sets.** Frozen.gd's own header comment says it
is "driven by the incoming hitbox," entered when a hit's
`grounded_hit_state`/`aerial_hit_state` reads `"Frozen"` instead of the
default hurt states. Confirmed by grep: that string is set only by
`characters/wizard/projectiles/telekinesis/TelekinesisIce.tscn`'s hitboxes
(Wizard's ice attack). No Ninja hitbox sets it. Since a Ninja mirror's only
hitboxes come from the two Ninjas, neither player can ever enter Frozen:
unreachable in Ninja vs Ninja **by construction of the match, not of the
tool** (Frozen is reachable in a real Ninja-vs-Wizard match). `_enter`
(34/35 unhit, one branch proven ghost-only), `_tick` (17/17), `_exit`
(11/12, one proven), `_frame_0` (2/2): ~64 probes.

These four buckets together account for roughly **750 of the 2,939** unhit
probes (263 + ~400 + 24 + ~64, the file/function totals above), on top of
the 68 individually-proven branch guards -- all backed by a grep-confirmed
caller or scene reference, not an assumption.

## Unhit probes by file and function (full table)

The complete table -- every (file, function) pair with an unhit probe, its
unhit count, how many of those are in the 68 proven-unreachable-by-guard
set, the remainder, and the function's total probe count -- is
`.scratch/free_coverage_report/unhit_by_file_function.tsv` (columns: file,
function, unhit, proven-unreachable, remainder, total-probes-in-function),
432 rows. The 40 largest by unhit count:

| File | Function | Unhit | Proven unreachable | Remainder | Total in func | What it handles |
|---|---|---:|---:|---:|---:|---|
| game.gd | ghost_tick | 116 | 0 | 116 | 116 | Ghost-prediction game's own per-frame update loop (bucket 1: tool never runs a ghost game). |
| game.gd | start_game | 71 | 8 | 63 | 241 | Match setup: characters, stage, settings, camera, achievements init. Core engine function, run by every match; its unhit lines are mostly multiplayer/replay/spectator/mod branches this corpus's singleplayer runs never take. |
| mechanics/SweptHitbox.gd | _swept_aabb_intersect | 66 | 0 | 66 | 66 | Continuous-collision core math (bucket 2: Mutant/Cowboy/Wizard only). |
| obj/BaseObj.gd | copy_to | 66 | 0 | 66 | 66 | Ghost-copy of one object's full state onto a prediction clone (bucket 1). |
| game.gd | _unhandled_input | 62 | 3 | 59 | 62 | Mouse/drag camera control and frame-advance debug input; a human-player-only UI path this tool's scripted input never drives. |
| game.gd | tick | 52 | 8 | 44 | 134 | The core per-tick simulation step (object ticking, hitlag, super-freeze). Reachable and heavily exercised; unhit lines are rare branch combinations (quitter focus, resimulate/ghost/network guards, specific freeze-frame counts). |
| game.gd | _physics_process | 49 | 4 | 45 | 111 | Per-physics-frame driver (pause, forfeit countdown, playback speed). Mostly reachable; unhit lines are forfeit-timeout and ghost/playback-speed branches. |
| characters/BaseChar.gd | tick_before | 48 | 2 | 46 | 177 | Pre-tick per-fighter bookkeeping (style aura, initiative, hitstun counters). Reachable; most unhit lines are specific aura/style states this corpus's inputs didn't reach. |
| projectile/DirProjectileDefault.gd | _tick | 47 | 0 | 47 | 73 | Default directional-projectile state's per-tick motion/collision. Ninja's Kunai/Shuriken-family projectiles use this; genuinely reachable but this corpus's random throws didn't exercise every branch (e.g. specific bounce/wall interactions). |
| game.gd | process_tick | 46 | 4 | 42 | 125 | Per-tick orchestration: hooks, super-freeze countdown, input application branch selection. Reachable; unhit lines are network/replay/spectator branches and some rare pacing combinations. |
| mechanics/SweptHitbox.gd | box_draw | 46 | 0 | 46 | 46 | Debug box-drawing for a swept hitbox (bucket 2). |
| characters/BaseChar.gd | _update_aura_threshold_trackers | 45 | 0 | 45 | 78 | Tracks style-aura meter thresholds for the aura particle system. Presentation-adjacent; genuinely reachable, not exercised by this corpus's play pattern. |
| game.gd | apply_hitboxes | 41 | 0 | 41 | 283 | The core hit-detection/resolution pass every attack goes through. Heavily exercised (41/283 unhit = 14.5%); remaining lines are specific interaction combinations (parries, counters, grabs, multi-hit) not all drawn by this corpus. |
| characters/BaseChar.gd | tick | 37 | 2 | 35 | 219 | Per-fighter per-tick update (movement, hitstun, meter). Heavily exercised; unhit lines are rare state combinations. |
| characters/states/Frozen.gd | _enter | 35 | 1 | 34 | 35 | Entering the frozen lockdown state on a freeze hitbox (bucket 4: Wizard-only hitbox property). |
| game.gd | copy_to | 29 | 0 | 29 | 29 | Top-level ghost-game state copy (bucket 1). |
| characters/BaseChar.gd | _aura_trigger_event_fired | 28 | 0 | 28 | 28 | Fires a style-aura particle event on a named trigger (movement/hit/etc). Genuinely reachable, presentation-adjacent, not exercised. |
| mechanics/SweptHitbox.gd | get_overlap_center_float | 28 | 0 | 28 | 28 | Swept-hitbox overlap center for hit-effect placement (bucket 2). |
| characters/states/CharState.gd | is_usable_with_grounded_check | 27 | 0 | 27 | 27 | Legality check combining `is_usable()` with a forced-aerial/forced-grounded override, called from the real action-menu UI (`ActionButtons.gd`) and `Legality.gd`'s cancel-option scan. Genuinely reachable in principle; not confirmed why this corpus's cancel-option scans never hit it (open item for a hand trace). |
| characters/BaseChar.gd | hit_by | 26 | 0 | 26 | 68 | Applying an incoming hitbox's damage/knockback/state-transition to this fighter. Heavily exercised (26/68 unhit); remainder are specific hit-type combinations. |
| characters/BaseChar.gd | launched_by | 26 | 0 | 26 | 108 | Applying launcher/knockback forces from a hit. Heavily exercised; remainder are specific force/angle combinations. |
| ObjectState.gd | _tick_shared | 24 | 0 | 24 | 146 | Shared per-tick bookkeeping every character state inherits (startup/feint flags). Heavily exercised (24/146); remainder are per-subclass override branches. |
| characters/BaseChar.gd | _apply_aura_state | 24 | 0 | 24 | 101 | Applies the current style-aura's visual/mechanical state. Presentation-adjacent, genuinely reachable. |
| game.gd | _process | 24 | 1 | 23 | 57 | Per-frame (not per-tick) update: camera dimming, UI. Mostly presentation; some ghost/UI-only branches. |
| ObjectState.gd | get_host_command | 23 | 0 | 23 | 23 | Looks up a scheduled "host command" (a deferred call queued for a future tick, e.g. delayed hitbox activation). Genuinely reachable; this corpus's specific move timings didn't trigger every lookup branch. |
| ObjectState.gd | has_upcoming_host_command | 22 | 0 | 22 | 22 | Same host-command mechanism, existence check. |
| mechanics/SweptHitbox.gd | _seg_seg_intersect | 22 | 0 | 22 | 22 | Segment-segment intersection, a SweptHitbox internal (bucket 2). |
| characters/states/Parry.gd | can_parry_hitbox | 21 | 0 | 21 | 21 | Whether the current parry window can parry a specific incoming hitbox (timing/type match). Genuinely reachable (Ninja has parry); this corpus's random parry attempts didn't line up with every hitbox type. |
| game.gd | get_colliding_hitbox | 21 | 0 | 21 | 88 | Finds which hitbox is overlapping a given hurtbox, among several candidates. Heavily exercised (21/88); remainder are multi-hitbox-overlap edge cases. |
| obj/BaseObj.gd | get_limb_local_dir | 21 | 0 | 21 | 21 | Limb-relative facing direction for IK/visual limb placement. Genuinely reachable, likely only used by specific limb-attached effects this corpus didn't trigger. |
| characters/states/CounterAttack.gd | is_usable | 20 | 0 | 20 | 20 | Legality check for entering a counter-attack state (timing/meter gate). Ninja's counter move exists; not confirmed why unhit (open item). |
| obj/BaseObj.gd | init | 20 | 0 | 20 | 20 | Base object one-time initialization. Surprising for something this central to be 100% unhit here -- likely a second, rarely-taken `init()` overload/branch set distinct from the per-match setup path already covered elsewhere (open item: confirm which `init` overload this is). |
| characters/states/Parry.gd | _tick | 18 | 0 | 18 | 18 | Per-tick update while holding a parry. Genuinely reachable; this corpus's parry attempts were short-lived or didn't hold the window long enough to hit every branch. |
| mechanics/Hitbox.gd | box_draw | 18 | 0 | 18 | 18 | Debug box-drawing for a plain (non-swept) hitbox. Presentation/debug only. |
| obj/BaseObj.gd | _limb_pixel_to_world | 18 | 0 | 18 | 18 | Limb pixel-space to world-space conversion, visual/IK support. |
| ObjectState.gd | is_hurtbox_active | 17 | 0 | 17 | 17 | Whether a given hurtbox is active at the current tick (start/end tick window check). Genuinely reachable; core hit-detection support. |
| characters/BaseChar.gd | check_initiative | 17 | 0 | 17 | 17 | Checks which player has "initiative" (a priority/clash rule) in a simultaneous-hit scenario. Genuinely reachable (clashes happen); this corpus's random play produced few or no exact-frame clashes. |
| characters/states/Frozen.gd | _tick | 17 | 0 | 17 | 17 | Per-tick update while frozen (bucket 4). |
| characters/BaseChar.gd | set_ghost_colors | 16 | 0 | 16 | 16 | Sets the ghost-prediction character's tint (bucket 1). |
| characters/states/CharState.gd | is_usable | 16 | 0 | 16 | 16 | Base legality check (meter/cooldown/state gates) most subclasses override. Base-class fallback; likely only reached by states that don't override it, which this corpus's move selection didn't trigger. |
| obj/BaseObj.gd | compare | 16 | 0 | 16 | 16 | Deterministic state-hash comparison, used by the determinism/fidelity checks (`tools/covsearch/corpus.py`, `tools/fgobject_diff`), not by normal play at all. |

The remaining ~392 functions (2,259 of the 2,939 unhit probes) were not
individually traced to this depth in this session; `unhit_by_file_function.tsv`
lists every one with its exact counts for whoever does that trace next.
Reading the table's `total-probes-in-function` column against `unhit`
separates "function barely used" (unhit close to total, like the ones
above) from "function heavily used, a handful of edge-case lines missed"
(unhit small relative to total) -- the plain, obvious distinction. From a
visual scan of the full table, the same four buckets above (ghost/copy
infrastructure, `SweptHitbox.gd`, the `randi_*`/`hash_*` RNG family, and
hitbox-property-gated shared states like `Frozen.gd`) recur through most
of the small-count entries too (for example `obj/BaseObj.gd`'s remaining
`get_limb_*`/`fixed_*`/`randi_*_static`/accessor functions, and
`characters/stickman/`'s own less-common moves such as `GrapplingHook`,
`StickyBomb`, `SwapPlaces`, `NunChuk`, `FlyingRoundhouse` whose followup
states depend on specific move sequences this corpus's random inputs
rarely chained into) -- stated here as an informed read of the table, not
as an individually proven classification for each one.

## Other characters vs Ninja-vs-Ninja-unreachable vs genuinely reachable

- **Code belonging only to another character's own directory
  (`characters/<name>/` for Cowboy/Wizard/Robot/Mutant):** zero probes.
  `instrument.py`'s `IN_SCOPE_GLOBS` only instruments `characters/states/*.gd`
  (shared) and `characters/stickman/**/*.gd` (Ninja); no other character's
  exclusive files were ever instrumented, confirmed by `probe_map.json`
  having no `characters/wizard/`, `characters/mutant/`, `characters/robo/`,
  or `characters/swordandgun/` entries. This category is empty by the
  probe set's own construction, not by this run's result.
- **Shared-directory code that only another character's moveset calls:**
  `mechanics/SweptHitbox.gd` (entire file, Mutant/Cowboy/Wizard), the
  `randi_*`/`hash_*` RNG family in `obj/BaseObj.gd` (Wizard's
  `ConjurePebble`), and `characters/states/Frozen.gd` (only enterable via a
  hitbox property Wizard's Telekinesis Ice sets). These live in
  instrumented shared directories but are unreachable in a Ninja mirror
  specifically because neither Ninja nor its mirror opponent ever
  constructs the object or sets the hitbox property that would reach them.
- **Unreachable by this tool's own construction, not a Ninja rule:** the
  ghost-prediction `copy_to`/`ghost_tick` family (bucket 1 above) and the
  68 proven branch guards (ghost/replay/network/spectate facts) -- these
  would also be unhit for `tools/replay_check`'s own generate/live modes,
  for any character pairing, because neither tool ever starts `main.gd`'s
  ghost game or a network/replay session.
- **Genuinely reachable but unhit by this corpus:** everything else --
  roughly 2,100-2,200 of the 2,939 unhit probes by the above accounting,
  concentrated in parry/counter timing windows, host-command-scheduled
  (delayed) hitbox activations, clash/initiative edge cases, and several
  of Ninja's own less-common moves' followup states (`GrapplingHook`,
  `StickyBomb`, `SwapPlaces`, `QuickSlash` followups, `NunChuk`/
  `NunChukJump`) that need a specific input sequence this corpus's random,
  independent per-turn choices rarely chained together. This is the real
  input to the next decision: a hand trace of `unhit_by_file_function.tsv`
  starting from this file's largest "genuinely reachable" entries, or a
  fuzzer biased toward chaining the specific move sequences these states
  need.

## A defect found, not fixed (named per the brief's scope: measurement only)

Seeds 3, 52, 63, 68, 142 crash `Caltrops.gd`'s `hit_by` with "Invalid get
index 'id' (on base: 'Nil')" on the plain, uninstrumented project. This is
a real gameplay bug in a committed Ninja move, reproduced 5 times in 199
random matches (~2.5% of matches). Out of this task's scope (measurement
only, no mutation/search loop); named here for whoever picks up the fuzz
or hand-trace decision next, since it already shows random play finding a
genuine defect.
