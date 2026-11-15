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
`BaseObject::apply_forces`); every other method is inlined into generic
gdnative dispatch machinery (`method_wrapper<F>`, `Stateless<F>::call`,
`ThisMethod::call`), one monomorphized instance per method, none of them
named after the method they belong to.

Every method's formula is confirmed one of two ways, both read from the
binary, not guessed from matching decimal displays:

- **A plain, non-inlined `BaseObject::*` method** (`apply_fric`,
  `apply_forces`, `get_data`): `tools/ghidra/container.sh` decompiles it by
  name directly.
- **A method inlined into the generic dispatch chain** (every other
  method, including `apply_x_fric`/`apply_y_fric`): found by following the
  chain one call at a time, each one resolved from the previous
  function's own disassembly (`objdump -d`, read for the one `call`
  instruction `tools/ghidra`'s decompiled C does not show a concrete
  address for), then decompiled: `nativeclass_register` (found by name)
  gives the registration order and each method's `method_wrapper`
  address; that wrapper's disassembly names `std::panicking::try`, itself
  monomorphized per method; `try`'s own disassembly names
  `Stateless<F>::call`; and that function's disassembly names the
  method-specific `ThisMethod::call`, which is the real method body
  (confirmed by its own `"FGObject :: <name>"` panic string). For
  `apply_x_fric`: `nativeclass_register` (`0x63b00`) → wrapper
  `0x32530` → `std::panicking::try` (`0x773f0`) → `Stateless<F>::call`
  (`0xfe80`) → `ThisMethod::call` (`0x69f00`, the real body). Every
  address is a file offset; Ghidra loads this binary at `+0x100000` (its
  own default base for a PIE/shared object), so `tools/ghidra/container.sh`
  was called with each address plus that offset.

Black-box probing in headless Godot (the same method `tools/fixedmath_diff`
used for `FixedMath`) filled in what decompilation does not show directly:
`get_data()`'s own output shape, and which stored field holds which
configuration value (the decompiled code reads raw struct offsets, not
field names).

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
- **`apply_fric`** (decompiled directly, `BaseObject::apply_fric`) divides
  `|vel_x|` by `max_speed` first (`max_ground_speed` when grounded,
  `max_air_speed` otherwise — this is `max_speed`, not `friction`: the
  decompiled code checks *this* field for zero before dividing by it,
  panicking on zero the same as a `Fixed::wrapping_div` by zero would),
  then multiplies that quotient by the matching `friction`
  (`ground_friction`/`air_friction`), then subtracts the signed result
  from `vel_x`. `vel_y` is untouched: a probed call with `vel_x = "5"`,
  `vel_y = "-3"` changed only `vel_x`. This division-then-multiply order
  is not the same as computing a ratio `friction / max_speed` once and
  multiplying `vel_x` by `1 - ratio`: the two are the same real number,
  but round differently at each step (the same lesson
  `tools/fixedmath_diff/README.md` documents for `lerp_string`'s
  formula) — only the decompiled order reproduces the engine's raw value
  exactly (`vel_x = "5"`, `ground_friction = "2.5"`,
  `max_ground_speed = "15"` gives raw `273068`, the ratio-first order
  gave `273070`). A zero `max_ground_speed`/`max_air_speed` is a division
  by zero in the real library, caught as Nil, with the object left
  unchanged.
- **`apply_x_fric(fric)`/`apply_y_fric(fric)`** (decompiled by chasing the
  dispatch chain to the method's own `ThisMethod::call`, see
  "Reconstruction method" above) compute `vel_x - wrapping_mul(fric,
  vel_x)` directly on the one axis named (the same formula as `v * (1 -
  fric)`, but subtract-after-multiply, not a precomputed `1 - fric`
  factor — another rounding-order difference), **but only store that
  result when `fric < 0` or `fric < |vel_x|`**: a positive `fric` at or
  past the current speed leaves the velocity unchanged, not clamped to
  `0`. Confirmed by probing against this exact rule: `fric = "2.5"` from
  `vel_x = "5"` applies (`2.5 < 5`) and gives `-7.5`; `fric = "10"`,
  `"15"`, `"100"` from the same `vel_x = "5"` do not (none is `< 5`) and
  leave `vel_x` at `5`; a negative `fric` of any magnitude, including the
  type's minimum, always applies.
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
  exposed by `get_data()`, but only while `|pushback| < 10.0`
  (`PUSHBACK_ADD_CAP` in `physics.rs`): past that magnitude the call is
  a no-op, the new `v` is dropped. Decompiled from `add_pushback`'s
  `ThisMethod::call` (`tools/ghidra`, address `0x167450`); see "Live
  stage" below for the chase from the registration table to that
  address, and for `apply_pushback`'s own decompiled decay rule (every
  call decays the accumulator by a fixed step, with no "already
  decayed" flag — this file's own earlier, probe-only belief in such a
  flag was wrong). `apply_pushback(dir)` moves `position_x` by
  `pushback * dir` (`dir` applied directly, with no dependence on the
  object's own facing). `reset_pushback()` zeroes the accumulator.
- **`copy_to(target)`** overwrites the target's entire state with the
  source's, field for field (including the hidden force accumulator and
  pushback state): probed by copying a source that had queued a pending
  force and a pushback, then driving the target through `apply_forces`/
  `apply_pushback` and seeing both take effect.
- **`reset_momentum()`** zeroes `vel_x` and `vel_y` only; position and
  every configuration field are unchanged.

## Open mismatches

`apply_x_fric`/`apply_y_fric`'s gate and `apply_fric`'s rounding order
(the two largest mismatch classes in the first pass of this tool) were
root-caused with `tools/ghidra` and fixed in `port/hustle_core/src/physics.rs`;
see the per-method notes above for each one's decompiled evidence. One
open mismatch remains, reported, not hidden, the same policy
`tools/fixedmath_diff` follows for its own two open mismatches
(`angle_dist`, `sign`):

**`apply_forces`/`apply_fric` with a value at the type's exact minimum
(`-140737488355328`).** The probe drives every argument to its edge
values, including this one: a force queued directly at the type's
minimum (`apply_force_str`/`apply_force_relative_str`), or a velocity
set directly to it (`set_vel`), then fed through `apply_forces` or
`apply_fric`. The real engine's own clamp code overflow-wraps on this
exact input (it normalizes-and-rescales a value that is "too big" via
a division, rather than a plain comparison-clamp, for reasons not
traced in the time this task had): `apply_forces` leaves `vel_y` at
`0` where the port's plain multiply-then-integrate changes it, and
`apply_fric` flips `vel_x`'s sign where the port does not. This is
reported, not fixed, because the triggering input is unreachable from
any real call site:
- No GDScript call site passes a force/velocity literal anywhere near
  this magnitude. Every `apply_force`/`apply_force_relative` call
  (and `_str` sibling) in `characters/`, `obj/`, and `mechanics/`
  passes a value built from a hitbox's `knockback` or a small
  per-character constant, run through `fixed.mul`/`fixed.div` by a
  modifier under `10`; none is a bare extreme literal.
- `set_vel` is never called with a literal anywhere near this
  magnitude either; every real caller passes a velocity already
  bounded by `max_ground_speed`/`max_air_speed`/`max_fall_speed`
  (every speed cap in the game's own data is under `25`), or a value
  copied from another object's own `get_vel()`.
- The stage itself is at most `1000` pixels wide, so even an
  accumulated position never approaches this magnitude from repeated
  small moves.

## The result

**1,343,932/1,343,937 calls matched exactly.** Run 2026-11-15, from an
empty `out/`: the edge-value probe (11,173 calls) plus all six census
replays (`.scratch/census_live/replays`, 1,332,764 calls), through
`container.sh`'s three stages end to end. Every one of the 5 remaining
mismatches is the one open issue above (a force at the type's exact
minimum fed through `apply_forces` or `apply_fric`), and every one is in
`probe.jsonl`: **all six live replay files match every call.**
First-divergence: 5 objects, 4 in `apply_forces`, 1 in `apply_fric`, all
probe-only. Earlier counts (1,388,629 and 1,399,802 calls) were wrong:
`container.sh` did not clear `out/`, so `probe.jsonl` held several stale
probe runs.

| Method | Calls | Matched |
|---|---:|---:|
| add_pushback | 871 | 871 |
| apply_force | 111 | 111 |
| apply_force_relative | 56 | 56 |
| apply_force_relative_str | 1,293 | 1,293 |
| apply_force_str | 6,128 | 6,128 |
| apply_forces | 23,237 | 23,233 |
| apply_forces_no_limit | 6,450 | 6,450 |
| apply_fric | 17,237 | 17,236 |
| apply_grav | 17,308 | 17,308 |
| apply_grav_custom | 3,890 | 3,890 |
| apply_pushback | 19,742 | 19,742 |
| apply_x_fric | 4,783 | 4,783 |
| apply_y_fric | 390 | 390 |
| copy_to | 18 | 18 |
| get_data | 371,235 | 371,235 |
| get_facing | 492,926 | 492,926 |
| get_snap_to_ground | 2 | 2 |
| get_x_vel_int | 13,657 | 13,657 |
| is_grounded | 238,739 | 238,739 |
| move_directly | 1,702 | 1,702 |
| move_directly_relative | 3,096 | 3,096 |
| move_directly_relative_str | 7,987 | 7,987 |
| move_directly_str | 3,441 | 3,441 |
| reset_momentum | 780 | 780 |
| reset_pushback | 3 | 3 |
| set_air_friction | 1,910 | 1,910 |
| set_facing | 9,601 | 9,601 |
| set_gravity | 1,909 | 1,909 |
| set_gravity_modifier | 565 | 565 |
| set_ground_friction | 1,959 | 1,959 |
| set_grounded | 986 | 986 |
| set_id | 536 | 536 |
| set_max_air_speed | 2,022 | 2,022 |
| set_max_fall_speed | 1,941 | 1,941 |
| set_max_ground_speed | 1,988 | 1,988 |
| set_position | 3,234 | 3,234 |
| set_position_str | 311 | 311 |
| set_snap_to_ground | 66 | 66 |
| set_vel | 2,652 | 2,652 |
| set_x | 4,796 | 4,796 |
| set_y | 5,128 | 5,128 |
| update_grounded | 69,251 | 69,251 |


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
  one case it could not resolve.
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

## Live stage: six real matches, and the first-divergence loop

`container.sh`'s stage 2 recorded six census replays, against
`localhost/hustle-replay-check`'s own live path, run through stage
3's comparator alongside the probe. The comparator's main loop for
this work is `report.json`'s `first_mismatches` (also printed to the
console): the first mismatching call per object, not every mismatch,
since once one object's state diverges, most of its later calls
mismatch too for the same root cause.

Seven formulas were wrong, each root-caused from a decompiled
`ThisMethod::call` body (`tools/ghidra`, chasing the dispatch chain
from `nativeclass_register` the same way this file's "Reconstruction
method" section documents for `apply_x_fric`), not from probing:

- `move_directly_str`/`move_directly_relative_str` (`ThisMethod::call`
  `0x6de00`/`0x6d960`): touch only `position_x`/`position_y`; they
  never zero `vel_y` or change `grounded`.
- `apply_forces_no_limit` (`ThisMethod::call` `0x68760`): `grounded`
  reads `position_y` from **before** this call's own move, not after.
- `apply_forces` (`BaseObject::apply_forces`, `nm` symbol): `vel_y`
  zeroes on landing only when the velocity that produced it was
  downward; a minimum-speed rule zeroes both `vel_x` and `vel_y`
  outright when `Fixed::vec_len(vel_x, vel_y)` is `6` raw units or
  less; a `vec_len` overflow (the previously-open
  pathological-`gravity_modifier` mismatch) leaves position and
  velocity already committed but the pending force uncleared.
- `apply_grav`/`apply_grav_custom` (`ThisMethod::call`
  `0x6a7a0`/`0x6ab30`): `apply_grav` is `apply_grav_custom` with the
  object's own `gravity`/`max_fall_speed`; neither gates on
  `gravity_modifier`'s sign, and `apply_grav_custom` also scales its
  `grav` argument by `gravity_modifier` (this crate's own earlier
  belief that it does not was a probe artifact: every probe left
  `gravity_modifier` at its default `1.0`).
- `apply_pushback` (`ThisMethod::call` `0x678b0`): decays the
  pushback accumulator by a fixed step on **every** call, with no
  "already decayed" flag; also re-applies the position-Y snap on
  every call.
- `apply_force_str` (`ThisMethod::call` `0x68b10`): drops a
  non-negative (downward or zero) `y` force outright while the
  object is grounded; only a negative (upward) `y` is queued while
  grounded. Found from the live replay's own first-divergence data
  (object `692`).
- `add_pushback` (`ThisMethod::call` `0x167450`, chased from the
  wrapper `0x3a730` through `std::panicking::try` `0x74250` and
  `Stateless<F>::call` `0x12740`, since `nativeclass_register`'s own
  registration order does not match the linker's symbol-table order
  for the 41 `ThisMethod::call` bodies `nm -C` lists): adds `v` to the
  accumulator only while `|pushback| < 10.0`; past that magnitude the
  call is a no-op. Found from the live replay's own first-divergence
  data (object `94` in `sick_ass_combo`, `apply_pushback(-1)` at line
  `39507`): several uncapped `add_pushback` calls in a row (a repeated
  hit-stun) ran the port's accumulator past what the real engine's
  capped one reached, so the next `apply_pushback` moved the object
  too far (`position_x` `810` against the engine's `804`).

Each fix's own commit (`git log` on `port-fgobject`) cites its
address and the exact recorded call that proved it.

**Result: every live call matches.** `1,343,932/1,343,937` calls
matched (probe plus all six live replays, from an empty `out/`).
First-divergence: 5 objects, 4 in `apply_forces` and 1 in `apply_fric`,
all in `probe.jsonl` — see "Open mismatches" above for why that residue
is unreachable from any real call site. No live replay file has a
first-divergence of any kind.


