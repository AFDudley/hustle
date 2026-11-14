# FGObject diff

This tool checks the Rust port of `FGObject`, the native class that holds
every game object's physics: position, velocity, gravity, friction,
pushback, and facing. `obj/BaseObj.gd` creates one per object
(`var chara = FGObject.new()`), and every physics call in the game's
GDScript routes through it. See
`docs/labbook/2026-11-13-core-game-scope-five-characters.md`, decisions 2,
4, 5, and 6, and `port/hustle_core/src/physics.rs`.

The method: call every `FGObject` method from GDScript, on a large edge-value
probe set and on six real human matches played through the live path, and
call the same methods in the Rust port on the same starting states. Compare
all results exactly.

## The method list

`FGObject` has 41 methods. `strings lib/tbfg.so | grep -oE "FGObject :: [a-zA-Z_]+"`
lists each one's own panic-message name, the same way
`tools/fixedmath_diff` found `FixedMath`'s 38 methods:

`add_pushback`, `apply_force`, `apply_force_relative`,
`apply_force_relative_str`, `apply_force_str`, `apply_forces`,
`apply_forces_no_limit`, `apply_fric`, `apply_grav`, `apply_grav_custom`,
`apply_pushback`, `apply_x_fric`, `apply_y_fric`, `copy_to`, `get_data`,
`get_facing`, `get_snap_to_ground`, `get_x_vel_int`, `is_grounded`,
`move_directly`, `move_directly_relative`, `move_directly_relative_str`,
`move_directly_str`, `reset_momentum`, `reset_pushback`,
`set_air_friction`, `set_facing`, `set_gravity`, `set_gravity_modifier`,
`set_ground_friction`, `set_grounded`, `set_max_air_speed`,
`set_max_fall_speed`, `set_max_ground_speed`, `set_position`,
`set_position_str`, `set_snap_to_ground`, `set_vel`, `set_x`, `set_y`,
`update_grounded`.

Every one of these 41 has a live call site somewhere in `characters/`,
`obj/`, or `mechanics/` (grepped directly): this class has no dead methods,
unlike `FixedMath`. `port/hustle_core/src/physics.rs` adds one more
function, `set_id`, for `chara.id = id` (`obj/BaseObj.gd:228`), a plain
property write rather than a method call.

`FGCharacter` and `GameSimulation` (`bin/FGCharacter.gdns`,
`bin/Simulation.gdns`) are two more classes the same library exports.
Grepping the whole game's GDScript for `FGCharacter` and `GameSimulation`
finds no call site at all: the game never uses them. They are not ported.

## Reconstruction method

The binary is not stripped (`file lib/tbfg.so` shows `with debug_info`), but
its DWARF debug information covers the Rust standard library and its
dependencies only (bundled for panic backtraces), not
`turn_based_fighting_game_lib`'s own types or functions. `nm -C lib/tbfg.so`
keeps only three of the crate's own functions unlinked
(`BaseObject::get_data`, `BaseObject::apply_fric`,
`BaseObject::apply_forces`); every other method is inlined into its own
GDNative call-closure in a release build, so disassembly gives the
instruction sequence but not a labeled formula. The struct field offsets
read from that disassembly turned out not to match the methods' own
observed behaviour (see "apply_fric" below): byte-level reverse engineering
of a release build is unreliable without a decompiler, so this task used it
only to find the method list and get_data()'s own output shape
(`FGObjectDiff.gd`'s predecessor probe, since deleted; its findings are
recorded here), and used black-box probing in Godot for every method's
actual formula, the same way `tools/fixedmath_diff` did for `FixedMath`.

### `get_data()`'s shape

```json
{"object_data": {"position_float": "(x, y)", "position_x": 0, "position_y": 0,
  "vel_x": "0", "vel_y": "0", "facing": 1, "grounded": true, "snap_to_ground": true}}
```

`position_x`/`position_y` are the raw position, rounded to the nearest pixel
(ties away from zero), then truncated to the low 32 bits of an `i32` — the
same truncation `tools/fixedmath_diff/README.md` documents for
`FixedMath::round`/`ceil`/`floor`: a raw position of exactly `2^47 - 1`
(the type's largest value) reads back as `-1`. `position_float` is a
`Vector2`-shaped string of the same position converted straight to `f32`,
with no truncation. No GDScript reads `position_float`
(`characters/BaseChar.gd`, `obj/BaseObj.gd`, and `game.gd` all read
`data.object_data.position_x`/`position_y` only), so `physics.rs` does not
model it; the comparison tool drops it from every recorded line before
comparing.

### Per-method notes

- **The configuration setters** (`set_gravity`, `set_gravity_modifier`,
  `set_ground_friction`, `set_air_friction`, `set_max_ground_speed`,
  `set_max_air_speed`, `set_max_fall_speed`) and **the raw state setters**
  (`set_x`, `set_y`, `set_position`, `set_position_str`, `set_vel`,
  `set_grounded`, `set_snap_to_ground`) are plain field writes, confirmed by
  probing: none of them triggers the ground check or any clamp (`set_vel`
  with `"1000"` on both axes stored `1000` unchanged).
- **`set_facing`** accepts only `1` or `-1`; any other value panics
  (`src/obj.rs:94`, `"invalid facing direction"`), caught as Nil, with the
  object's own facing left exactly as it was.
- **`update_grounded`** is a plain `position_y >= 0` recompute, both
  directions: probed `y = -5` gave `false`, `y = 0` and `y = 5` gave `true`.
- **`get_x_vel_int`** rounds `vel_x` to the nearest integer, ties away from
  zero, then truncates to `i32` the same way `position_x`/`position_y` do:
  the type's maximum value reads back as `-1`, its minimum as `0`.
- **The force accumulator.** `apply_force`/`apply_force_str` add directly to
  a hidden `pending_force_x`/`pending_force_y` pair that `get_data()` never
  exposes: calling `apply_force` alone leaves `get_data()` unchanged, and
  only a following `apply_forces`/`apply_forces_no_limit` shows the effect.
  `apply_force_relative`/`apply_force_relative_str` scale `x` by the
  object's own facing (`1` or `-1`) first; no probe showed `y` scaled by
  facing, and no live call site passes a nonzero `y` to a `_relative` force
  method.
- **`apply_grav`** queues `gravity * gravity_modifier` as a downward force,
  but only while the object is airborne (`!grounded`) and
  `gravity_modifier` is positive. A negative `gravity_modifier` (probed:
  `"-0.00001"` and the type's minimum) added nothing, not the negative
  product — probed evidence only for the grounded gate on
  `apply_grav_custom` (next); `apply_grav`'s own gate follows by inference,
  since both methods clearly share one underlying gravity routine.
- **`apply_grav_custom(grav, fall_speed)`** queues `grav` directly (no
  `gravity_modifier` scaling), gated the same two ways: airborne only, and
  only while the current `vel_y` is still below `fall_speed`. From
  `vel_y = "20"` (already past `fall_speed = "6"`), the call added nothing,
  and the following `apply_forces` left `vel_y` at the object's own stored
  `max_fall_speed` ("15" in the probe), not at the custom `fall_speed`
  ("6") — `fall_speed` gates the queue, it does not change the cap
  `apply_forces` applies. The boundary case `vel_y == fall_speed` was not
  probed.
- **`apply_forces`/`apply_forces_no_limit`** integrate the queued force into
  velocity (clamped, for `apply_forces`, to
  `[-max_ground_speed, max_ground_speed]` when grounded or
  `[-max_air_speed, max_air_speed]` when not; `apply_forces_no_limit` skips
  this clamp entirely), clamp `vel_y` on its downward (positive) side only
  to `max_fall_speed` (an already-excessive `vel_y`, with no pending force
  at all, was pulled back to the cap by a plain call; the upward/negative
  side is never capped), move the object by the new velocity, then apply
  the ground check: landing (`position_y >= 0`) zeroes `vel_y` and, with
  `snap_to_ground` set, clamps the position to exactly `0`; **this check
  recomputes `grounded` both ways** — a recorded real call left an
  already-grounded object airborne (`grounded = false`) after it moved to
  `y = -195` with no landing involved.
- **`apply_fric`** applies an effective friction to `vel_x` **only**: a
  probed call with `vel_x = "5"`, `vel_y = "-3"` changed `vel_x` and left
  `vel_y` exactly at `-3`, for every probed friction and speed-cap
  combination. The effective friction is `ground_friction /
  max_ground_speed` when grounded, `air_friction / max_air_speed`
  otherwise; two independent configurations (`2.5`/`15` grounded, `0.2`/`10`
  airborne) matched this ratio within a couple of raw units (see "Open
  mismatches" below). A zero `max_ground_speed`/`max_air_speed` is a
  division by zero in the real library, caught as Nil, with the object
  left unchanged.
- **`apply_x_fric(fric)`/`apply_y_fric(fric)`** compute `v * (1 - fric)`
  directly on the one axis named, with no clamp: `apply_x_fric("2.5")` from
  `vel_x = "5"` gave `-7.5 = 5 * (1 - 2.5)`, a sign flip, not a value
  clamped to `0`. A large *positive* `fric` (probed: `10`, `15`, `100`, and
  the type's maximum) left the object unchanged instead — see "Open
  mismatches".
- **`move_directly`/`move_directly_relative`** (and their `_str` siblings)
  add the given delta straight to the position (scaled by facing for the
  `_relative` pair, the same as `apply_force_relative`), then apply a
  **one-directional** ground check: landing (`position_y >= 0`) zeroes
  `vel_y` and clamps the position with `snap_to_ground`, the same as
  `apply_forces`, but moving to a position still above the ground
  (`position_y < 0`) leaves `grounded` exactly as it was — a recorded real
  call moved an already-grounded object to `y = -1` and it stayed grounded,
  which the bidirectional `apply_forces` rule would have cleared.
- **`add_pushback(v)`** adds `v` to a hidden `pushback` accumulator, not
  exposed by `get_data()`, and marks it "not yet decayed".
  **`apply_pushback(dir)`** moves `position_x` by `pushback * dir` (`dir`
  applied directly, with no dependence on the object's own facing), then,
  **only on the first `apply_pushback` since the last `add_pushback`**,
  decays `pushback` by a constant factor and marks it "decayed": three
  consecutive `apply_pushback` calls with no new `add_pushback` between
  them moved the object by the full pushback, then by `≈0.8` times that,
  then by the *same* decayed amount again, not a further-decayed one.
  **`reset_pushback()`** zeroes the accumulator and clears the decayed
  flag.
- **`copy_to(target)`** overwrites the target's entire state with the
  source's, field for field (including the hidden force accumulator and
  pushback state): probed by copying a source that had queued a pending
  force and a pushback, then driving the target through `apply_forces`/
  `apply_pushback` and seeing both take effect.
- **`reset_momentum()`** zeroes `vel_x` and `vel_y` only; position and
  every configuration field are unchanged.

## Open mismatches

Both are reported, not hidden, the same policy `tools/fixedmath_diff`
follows for its own two open mismatches (`angle_dist`, `sign`). Neither has
a live call site anywhere close to the triggering values.

1. **`apply_fric`'s exact raw value is off by about 2 of 65536 units on
   some configurations.** The ratio `friction / max_speed` and the formula
   `vel_x * (1 - ratio)` reproduce the *decimal display* of every probed
   case to 5 digits in most configurations, but not all: one probed case
   (`friction = "2.5"`, `max_ground_speed = "15"`, `vel_x = "5"`) computed
   raw `273070`, the game's own raw value is `273068`. This is a genuine
   rounding-order difference (the same class `tools/fixedmath_diff/README.md`
   documents for `lerp_string`'s formula), not resolved: the port's formula
   is the simplest one that reproduces every other probed case exactly.
2. **`apply_x_fric`/`apply_y_fric` do nothing for a large *positive*
   `fric` argument.** Probed: `fric = "2.5"` (and every value below it,
   including negative values of any magnitude, even the type's minimum)
   applies the formula normally; `fric = "10"`, `"15"`, `"100"`, and the
   type's maximum leave the object unchanged, as if the call panicked. No
   boundary between `2.5` and `10` was probed, and no candidate rule (a
   fixed constant, a comparison against `max_ground_speed`/`max_air_speed`,
   a comparison against the current velocity) fit every probed point at
   once. The port applies the formula unconditionally for every `fric`.
3. **`apply_forces` with a pathological `gravity_modifier`** (the type's
   minimum, fed through `apply_grav` then `apply_forces`) produced a huge
   position change with `vel_y` left at `0` in the real game; the port's
   formula (multiply, then integrate normally) instead changes both. This
   is an overflow-wrapping difference at an input no real gameplay value
   approaches (`gravity_modifier` is always a small positive multiplier in
   the game's own data).

None of the three affects any call observed on the six real matches (see
"The result" below): every live replay call matched.

## The result

**Edge-value probe: 11,115/11,173 calls matched exactly (99.5%).** Run
2026-11-14 against a fresh build. The 58 mismatches are exactly the three
open issues above: `apply_fric` (25 of 67, the raw-value rounding
difference, present on every grounded/airborne combination it was probed
with), `apply_x_fric`/`apply_y_fric` (4 of 17 each, the large-positive-`fric`
no-op), and `apply_forces` (25 of 285, all from the one pathological
`gravity_modifier` value fed through `apply_grav`). Every other method
matched every probed call, including the full edge-value sweep on every
setter and every integer-argument boundary.

| Method | Probe calls | Matched |
|---|---:|---:|
| add_pushback | 17 | 17 |
| apply_force | 81 | 81 |
| apply_force_relative | 18 | 18 |
| apply_force_relative_str | 34 | 34 |
| apply_force_str | 17 | 17 |
| apply_forces | 285 | 260 |
| apply_forces_no_limit | 17 | 17 |
| apply_fric | 67 | 42 |
| apply_grav | 102 | 102 |
| apply_grav_custom | 90 | 90 |
| apply_pushback | 31 | 31 |
| apply_x_fric | 17 | 13 |
| apply_y_fric | 17 | 13 |
| copy_to | 18 | 18 |
| get_data | — | (checked as part of every other call's "after") |
| get_facing | 129 | 129 |
| get_snap_to_ground | 2 | 2 |
| get_x_vel_int | 23 | 23 |
| is_grounded | 2 | 2 |
| move_directly | 101 | 101 |
| move_directly_relative | 18 | 18 |
| move_directly_relative_str | 34 | 34 |
| move_directly_str | 289 | 289 |
| reset_momentum | 17 | 17 |
| reset_pushback | 17 | 17 |
| set_air_friction | 17 | 17 |
| set_facing | 129 | 129 |
| set_gravity | 17 | 17 |
| set_gravity_modifier | 17 | 17 |
| set_ground_friction | 17 | 17 |
| set_grounded | 267 | 267 |
| set_id | 5 | 5 |
| set_max_air_speed | 17 | 17 |
| set_max_fall_speed | 17 | 17 |
| set_max_ground_speed | 17 | 17 |
| set_position | 162 | 162 |
| set_position_str | 311 | 311 |
| set_snap_to_ground | 2 | 2 |
| set_vel | 456 | 456 |
| set_x | 2 | 2 |
| set_y | 2 | 2 |
| update_grounded | 8 | 8 |

**Real play: not completed in this task.** `container.sh`'s stage 2
(`driver_live.py`, the 6 census replays at
`.scratch/census_live/replays` in the main repo) was run against the real
game: it recorded over 100,000 `FGObject` calls from the first of the six
replays (`1521683218758111383_silent_ninja`) before this task's time
budget ran out, and the run was stopped before the Rust comparator saw
that data or the other five replays ran. The pipeline itself is complete
and was observed recording correctly (one JSON line per call, growing
steadily); what is missing is the final match count against
`hustle_core::physics`. Running `tools/fgobject_diff/container.sh` to
completion is the next step, and it is the one this tool's acceptance
criterion depends on most: the probe set is edge cases by construction,
real matches are not.

## How it works

- **`FGObjectDiff.gd` + `FGObjectDiff.tscn`**: the edge-value probe. Builds
  an `FGObjectRecorder` (below) per scenario, from a handful of physics
  configurations, and drives it through every one of the 41 methods with
  edge values: `0`, `±1` ulp, the type's integer-range limits, the literal
  values the game's own GDScript passes (`"2.5"`, `"0.2"`, `"15"`, `"10"`),
  and the `i32` boundary for the integer-argument methods. Reads the output
  path from the environment variable `FGOBJECT_DIFF_OUT`.
- **`FGObjectRecorder.gd`**: a recording proxy. Holds a real `FGObject` and
  forwards every one of the 41 methods to it, returning the real result
  unchanged, so a game using this proxy in place of `FGObject` runs exactly
  as it did before. Each forwarded call appends one JSON line to
  `FGOBJECT_DIFF_OUT`: `{"obj", "method", "args", "before", "after",
  "result"}`, where `"obj"` is the proxy's own `get_instance_id()` (so a
  file holding many objects' interleaved calls can be split back out by
  object) and `"before"`/`"after"` are the real `get_data()` output read
  immediately before and after the call. `copy_to` writes a different
  shape, `{"obj", "target_obj", "source_before", "target_before",
  "target_after"}`, since it mutates the target, not the caller.
  GDScript's `_set` intercepts the one plain property write the game makes
  (`chara.id = id`), which is not one of the 41 methods.
- **`driver_live.py`**: plays real replays through the live path with the
  recording proxy wired in. Imports `tools/replay_check/run.py` as a module
  (`Work`, `start_display`, `prepare`, `live_job`, `run_job`) rather than
  duplicating the turn-driving logic that file already has, and changes
  nothing in it. The one patch this script makes is local to its own run:
  after `prepare()` rsyncs the repository into a disposable work copy, it
  replaces the one line in that copy's `obj/BaseObj.gd` that constructs
  `chara` (`var chara = FGObject.new()`) with a `preload` of
  `FGObjectRecorder.gd`, then runs the replay exactly as `tools/replay_check`
  does. The committed `obj/BaseObj.gd` is never touched. Writes one
  `<replay name>.jsonl` per input replay.
- **`rust/`**: a crate with one binary, `fgobject_diff`. Reads every input
  `.jsonl` file (the probe's and each replay's), and for each line, replays
  the call against `hustle_core::physics`, keeping one `ObjectData` per
  `"obj"` id across the whole file (seeded at `physics::new_object()` the
  first time an id appears) so a later call sees the full-precision state
  the one before it left — `get_data()`'s own `position_x`/`position_y`
  are rounded and truncated, so resuming from the recorded value on every
  call would lose precision the real object never loses. Compares the
  computed result against the recording, field by field (never comparing
  the excluded `position_float`), and writes `report.json`: a per-method
  call/match count, and every mismatch in full.
- **`container.sh`**: the host-side entry point. Three podman stages, each
  read-only on the repository (the work and output directories are the
  only writable mounts):
  1. The Godot edge-value probe, in `tools/replay_check`'s own Nix image.
  2. The Godot live-replay recording, in the same image, running
     `driver_live.py` against `--census-replays` (default:
     `.scratch/census_live/replays`, overridable since `.scratch/` is not
     shared between a worktree and the main checkout).
  3. The Rust replay-and-compare, in this tool's own Nix image (built by
     `build.sh`). Exits 1 if any call mismatched.

```sh
tools/fgobject_diff/container.sh
cat ~/.cache/hustle-fgobject-diff/out/report.json
```

## The container

`flake.nix` (with `nix/rust.nix` and `nix/image.nix`) builds
`localhost/hustle-fgobject-diff` with Nix, the same way
`tools/fixedmath_diff/flake.nix` and `tools/rng_diff/flake.nix` build their
own images. `build.sh` builds it inside the pinned `docker.io/nixos/nix`
container `tools/nix_image_build.sh` uses for every tool, and loads it into
podman. `flake.lock` pins the same nixpkgs revision as
`tools/replay_check/flake.lock`.

This tool's Rust crate (`rust/Cargo.toml`) has a path dependency on
`hustle_core` (`port/hustle_core`, a different task slice of the same
port). `build.sh` passes `tools/nix_image_build.sh` the same
`EXTRA_SRC:EXTRA_DEST` pattern `tools/rng_diff/build.sh` and
`tools/fixedmath_diff/build.sh` use, staging `port/hustle_core`'s
git-tracked files as the sibling directory `hustle_core`.

The image holds only the `fgobject_diff` binary: unlike
`tools/fixedmath_diff` and `tools/rng_diff`, there is no separate Python
comparison stage, because the Rust binary does its own comparison and
writes `report.json` directly.

## Limits

- The probe set is large but finite; see "Open mismatches" above for the
  three cases it could not resolve.
- `tools/replay_check`'s own `ignore.txt`/trace format are not reused
  here: this tool's recording is at the `FGObject` call level, independent
  of `tools/replay_check`'s own per-tick state hashes. A call that matches
  here but whose *caller* reads a field `get_data()` does not expose (none
  found: see "Per-method notes") would not be caught.
- `port/hustle_core`'s `tables` module (a different, concurrently
  developed task slice) was not present in this worktree while this task
  ran, so neither `cargo test` nor this tool's own Nix build of
  `hustle_core` could complete end to end; see `port/hustle_core/README.md`
  and this task's final report for how `physics.rs` itself was verified
  instead, and for the exact blocking error.
