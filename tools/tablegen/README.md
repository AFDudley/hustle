# Table generator

This tool writes one slice of the Rust port of Your Only Move Is HUSTLE: the
five official characters' data. See
`docs/labbook/2026-11-13-core-game-scope-five-characters.md`, decisions 1,
5, 6, and 9, and `port/hustle_core/README.md`.

The method: `gen.py` reads the game's own `.tscn` scene files and `.gd`
scripts, resolves Godot's scene inheritance and GDScript class chains the
same way the engine does, and writes the result as plain Rust `struct`s,
`enum`s, and `const`/`static` data in `port/hustle_core/src/tables/`. A
headless Godot probe (`TableGenProbe.gd`) reads the same fields from the
real, loaded scenes, and `compare.py` checks every one against a dump of
the generated tables (`tools/tablegen/dump/`, a separate crate). This is
data only: `_tick`/`_frame_N` behavior code stays in GDScript for a later
slice.

## Running it

```sh
tools/tablegen/container.sh
cat ~/.cache/hustle-tablegen/out/report.json
```

Five podman stages: generate (writes the tables, the `table_dump` source,
`manifest.json`, and the Lean table `def`s and `TableDump` program to a
scratch directory), copy that output into the repository and `git add`
it, dump (builds and runs `table_dump`), probe (a headless Godot run
against the real scenes), and compare. No stage runs Python, Godot, or
`cargo` on the host.

## The result

**100,304/100,304 fields matched exactly, across 1,108 records (636
state records, 449 hitbox records, 23 root records). 0 mismatches.**
Checked by running the five stages above on a checkout at commit
`9d0d8b27`. `report.json` holds the same counts per scope.

Running the generator twice (`gen.py`, independently, in two separate
containers) gives byte-identical output in `port/hustle_core/src/tables`,
`tools/tablegen/dump/rust/src/main.rs`, and `manifest.json`.

## What it covers

| | Count |
|---|---:|
| Character scopes (the base plus the five characters) | 6 |
| Projectile scopes (found by scanning every character's `projectile_scene` reference, recursively) | 17 |
| States, across every scope | 636 |
| Hitboxes (`Hitbox`, `ThrowBox`, `SweptHitbox` children of a state), across every scope | 449 |
| Cancel-category tokens (found by scanning every `interrupt_from_string`/`interrupt_into_string`/`hit_cancel_into_string`/`interrupt_exceptions_string`/`hit_cancel_exceptions_string`) | 68 |
| Stance tokens | 6 |
| `misc_data` tags | 2 |
| `trigger_orb_type` tokens | 4 |

These are all found by scanning the real `.tscn` data, not a hand-written
list: a token this generator has never seen becomes a new enum variant
automatically, not a silent gap.

## The schema

`schema.py` is the one place that names which `export var` fields a table
keeps, and which it drops. Each kept field carries a type tag (`bool`,
`int`, `fixed`, an enum, a state reference, …); `gen.py`'s `rust_type`
and `emit_value` turn a tag into a Rust type and a Rust literal.

### Roles

- **Fighter root** (`FIGHTER_ROOT_FIELDS`): the character's own exported
  fields (`characters/BaseChar.gd`, `obj/BaseObj.gd`) — gravity,
  friction, max speeds, air option count, and similar.
- **Projectile root** (`PROJECTILE_ROOT_FIELDS`): the same idea for a
  projectile scene's root (`projectile/BaseProjectile.gd`, `obj/BaseObj.gd`).
- **State** (`OBJECT_STATE_FIELDS`, shared by every state; plus
  `CHARACTER_STATE_FIELDS`, `SUPERMOVE_FIELDS`, `THROWSTATE_FIELDS`,
  `GRAB_FIELDS`, and one of `ROBOTSTATE_FIELDS`/`BEASTSTATE_FIELDS`/
  `WIZARDSTATE_FIELDS` for a character state only): frame data, forces,
  cancel categories, super/throw/grab data.
- **Hitbox** (`HITBOX_FIELDS`, plus `THROWBOX_FIELDS`/`SWEPTHITBOX_FIELDS`
  for the one or the other): damage, knockback, frame data, block data.

A character's `StateData`/`HitboxRecord` struct is the union of every
mixin any of that character's own states actually uses (the base, plus
`RobotState` for Robot, `BeastState` for Mutant, `WizardState` for
Wizard); a state that does not use a given mixin still carries that
mixin's own declared default in those columns — the real game would show
the same default if that state *were* of that class, since GDScript
always carries every ancestor's exported field. `compare.py` does not
check such a column against the engine: the engine has no property there
to compare (see `compare.py`'s own docstring).

### Kept fields, by role

See `schema.py`'s `OBJECT_STATE_FIELDS`, `CHARACTER_STATE_FIELDS`,
`SUPERMOVE_FIELDS`, `THROWSTATE_FIELDS`, `GRAB_FIELDS`, `ROBOTSTATE_FIELDS`,
`BEASTSTATE_FIELDS`, `WIZARDSTATE_FIELDS`, `HITBOX_FIELDS`,
`THROWBOX_FIELDS`, `SWEPTHITBOX_FIELDS`, `FIGHTER_ROOT_FIELDS`, and
`PROJECTILE_ROOT_FIELDS` for the full, exact lists (field name and type
tag). `manifest.json`'s `field_tags` is the same list, generated, for the
probe and `compare.py` to read at run time.

### Dropped fields, and why

Every drop is recorded once, next to the field list it would have joined,
in `schema.py`'s `OBJECT_STATE_DROPPED`, `CHARACTER_STATE_DROPPED`,
`SUPERMOVE_DROPPED`, `THROWSTATE_DROPPED`, `ROBOTSTATE_DROPPED`,
`HITBOX_DROPPED`, `THROWBOX_DROPPED`, `SWEPTHITBOX_DROPPED`,
`FIGHTER_ROOT_DROPPED`, and `PROJECTILE_ROOT_DROPPED`. The reasons fall
into a few groups:

- **Presentation**: a texture, sprite animation name or frame-loop
  setting, particle scene, screenshake cue, audio clip (and its volume,
  pitch jitter, or overlap rule), UI icon or label, palette swap, or
  controller rumble. None of these change game state.
  `enable_extra_aesthetic_hitstop`'s own name says so.
- **An inspector category marker**: every `_c_*` field (`_c_Physics`,
  `_c_Damage`, …). Godot's exported-variable inspector groups fields
  under these; they hold the constant `0` and nothing else.
- **Always empty in the five characters' own data, checked**:
  `host_commands` (every state), `throw_positions` at the Fighter/
  BaseObj root level (only the per-state field, which is kept, ever
  carries real data), and the projectile root's own `throw_positions`
  (projectiles are never grabbed).
- **No counterpart in the port's state**: `extra_state_variables` names
  which of the GDScript object's own non-exported variables the live
  engine's rollback/replay system must snapshot. The port's state is
  already fully explicit (decision 9's pure `step` function), so there is
  no "variable it forgot to track" for this field to name.
- **Assigned at runtime, not per-character data**: `id` (set at match
  setup), `dummy` (an editor/debug flag).

### Leaf-only move scripts (out of scope for this slice)

32 of the 147 character-specific move scripts declare their own
`export var`s beyond the shared classes above (for example `DiveKick.gd`'s
`move_x`/`move_y`/`grounded`, `AirDash.gd`'s (SwordGuy's own)
`FORWARD_FORCE_X`/`startup_invuln`/..., `WallTrickFollowup.gd`'s
`move_x_`/`move_y_`/`move_speed`). Every one of these feeds only that one
move's own `_frame_N`/`_tick` code, which decision 9 defers to a later
slice — this slice is data only, and porting a parameter with no code to
read it yet has no value. The full, current list (file and field names)
is reproducible by re-running the survey this README's own counts came
from:

```sh
grep -rl '^export' characters/*/states/*.gd
```

## Two data anomalies, found by the generator, kept as data

The generator never guesses a value. Two "fixed" fields in the real
`.tscn` data are not decimal numbers at all:

- `characters/wizard/Wizard.tscn`'s `ConfusingTouch2` sets both its
  hitboxes' `dir_y = ""`.
- `characters/robo/Robot.tscn`'s `JumpGrab` `ThrowBox` sets
  `block_pushback_modifier = "False"`.

`fixed_parse` (`tools/tablegen/rust`) reports both `INVALID`, the same way
`hustle_core::fixed::Fixed::from_bytes` — the exact function the port's
tables use — would reject them. The generated tables store `None`
(`Option<Fixed>`) for these two fields, not a guessed number, and
`compare.py` checks that the probe's raw value is one `fixed_parse` also
calls invalid, so a future change that makes either value parseable (or
adds a new invalid one elsewhere) is still checked, not silently passed.

Two `throw_positions` entries are also confirmed dead data, each named
and cited at its exact check in `gen.py` (`resolve_throw_positions` and
`resolve_ext_props`):

- `characters/stickman/NinjaGuy.tscn`'s `HitGrabFollowup` keys a position
  by `hitgrab_followup6.png`, a real texture file that the state's own
  `HitGrabFollowup` sprite animation never shows (it cycles five other
  textures, not six).
- `characters/swordandgun/SwordGuy.tscn`'s `UpwardSwipeAir` sets
  `throw_positions = { 12: "try_shoot" }`: not a texture key, and not an
  `{x, y}` position. This is `ObjectState.host_commands`-shaped data
  (tick → method name) written to the wrong exported field; `UpwardSwipe.gd`
  extends `CharacterState` directly, not `ThrowState`, so
  `update_throw_position` (the only reader of `throw_positions`) never
  runs for this state either way.

Any other texture key that matches no frame, or any other non-texture key,
still stops the generator with the exact state and key named — these two
are the only ones the five characters' current data has.

## `throw_positions`: resolved through the sprite, not a tick number

`ThrowState.gd`'s `update_throw_position` reads `throw_positions` keyed by
the texture **currently showing** (`host.get_current_sprite_frame()`
returns the `Texture` resource, not an integer), not by a tick. `gen.py`'s
`resolve_throw_positions` walks the character's own `SpriteFrames`
resource (resolved from the `Sprite` node's `frames` property, `Flip/
Sprite` in every scope) and records every `(animation, frame index)` a
texture appears at — a texture can repeat across frames (a held pose) — so
the table stores `(animation, frame, x, y)` tuples, matching exactly what
the engine can show. `TableGenProbe.gd`'s `resolve_throw_positions`
computes the same mapping independently, by walking every real animation
frame of the loaded `SpriteFrames` and checking which ones are a
`throw_positions` key; `compare.py` checks the two as sets (order is not
meaningful).

## How it works

- `godot_scene.py`: a generic reader for Godot 3's `.tscn` property
  syntax (numbers, quoted strings that may hold a literal newline,
  `Ident(args)` calls, arrays, dictionaries) and a `.gd` script's
  `extends`, `class_name`, and `export var` declarations (including a
  multi-line default value). Knows nothing about any one state or
  character.
- `schema.py`: the field manifest (above), Godot's own fixed enums
  (`ActionType`, `AirType`, `BusyInterrupt`, `HitboxType`, `HitHeight`),
  and GDScript class-chain resolution (`extends ClassName` → the file
  that declares `class_name ClassName`, root to leaf).
- `gen.py`: resolves Godot's scene-inheritance rule (a character's
  `.tscn` instances `BaseChar.tscn`; a projectile's instances
  `projectile/BaseProjectile.tscn`) generically — the same merge
  algorithm handles both, and recurses through every `projectile_scene`
  reference it finds, so a projectile that spawns another projectile
  would be found too. Classifies every node by its resolved script's
  class chain (does it reach `CharacterState`? `CollisionBox`? `Hitbox`?
  `ThrowBox`? `BaseProjectile`?), picks the matching field list, and
  writes the Rust tables, the `table_dump` source, and `manifest.json`
  from the one pass.
- `TableGenProbe.gd`/`.tscn`: a generic Godot probe. Reads field names
  from `manifest.json`, not a hand-written per-class list, so the probe
  and the generator cannot name different fields by accident. Loads each
  scene named in the manifest, walks its `StateMachine`, and writes one
  JSON line per state, hitbox, and root record.
- `dump/`: a separate Rust crate (`table_dump`), generated by `gen.py`
  alongside the tables, that prints every value `hustle_core::tables`
  holds as JSON Lines. It depends on `hustle_core` with its default
  (no-feature) build — the same build Charon analyzes — so a mismatch
  between what Charon sees and what this binary reads cannot happen by
  construction.
- `HustleCorePortFunsExternal.lean` and `HustleCorePortTableDump.lean`
  (written into `tools/lean_check/generated/`): a Lean `def` for every
  table item Charon marks opaque for the lean-check pipeline, and a
  Lean program that prints those `def`s in `table_dump`'s own JSON
  Lines format. `emit_value_lean` mirrors `emit_value` field for
  field, from the same `raw` data, so the Rust and Lean tables cannot
  hold different values by construction. See `tools/lean_check/README.md`,
  "port/hustle_core, the tables slice", for how the lean-check pipeline
  uses these two files and the Rust/Lean equality check it runs.
- `compare.py`: joins the two JSON-Lines files by `(scope, record,
  state, hitbox_index)`, not by line position (the engine's own child
  order and the generated Rust's declaration order need not match), and
  checks every field `field_tags` says both sides should carry. See its
  own docstring for exactly how each type tag compares (a "fixed" field
  through `fixed_parse`; a category/stance field as a set of tokens; an
  optional field's empty string treated as the generated side's `null`).

## The container

Two images, both built the way every tool in this repository builds one
(`tools/nix_image_build.sh`; see `tools/rng_diff/README.md`, "The
container", for the shared steps):

- `localhost/hustle-tablegen` (`flake.nix`, `nix/rust.nix`,
  `nix/image.nix`, `build.sh`): Python 3, and the `fixed_parse` binary.
  `fixed_parse`'s crate (`rust/`) has a path dependency on `hustle_core`,
  staged the same way `tools/rng_diff`'s does.
- `localhost/hustle-table-dump` (`dump/flake.nix`, `dump/nix/rust.nix`,
  `dump/nix/image.nix`, `dump/build.sh`): the `table_dump` binary. Its
  `src/main.rs` is generated code that names every table in
  `port/hustle_core/src/tables`, so this image can only build *after*
  the generator has written the tables and `container.sh` has `git add`ed
  them (`tools/nix_image_build.sh` stages git-tracked files only) — a
  second image, not a second binary in the first tool's own crate, is
  what makes `container.sh`'s ordering (generate, then copy and `git
  add`, then dump) possible at all. `port/hustle_core/src/tables/mod.rs`
  starts as an empty placeholder in a fresh checkout, specifically so
  `fixed_parse`'s own build (which needs `hustle_core` to compile, but
  not the tables themselves) never depends on the generator having run
  first.
- The probe reuses `tools/replay_check`'s own image
  (`localhost/hustle-replay-check`) and its `run.py prepare` step, which
  mirrors the repository into a writable, asset-imported project copy
  (Godot's headless resource loader needs the `.import/` cache that step
  creates; a read-only `/repo` mount cannot hold it). `container.sh`
  runs that step before the probe, the same way `tools/replay_check`'s
  own `container.sh` does.

## Limits

- This slice ports data only. The cancel-category, state-reference, and
  hitbox-geometry fields are all here, but the `step` function that reads
  them is a later slice.
- The 32 leaf-only move scripts' own fields are out of scope (see above).
  A later slice that ports per-move behavior will need to extend this
  generator's schema for them.
- `throw_positions` and the two Fixed-value anomalies are the only
  non-literal data this generator found; any new one the game's data
  gains later stops the generator, by design, rather than guessing at a
  value.
