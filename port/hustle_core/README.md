# hustle_core

This crate is the start of a Rust port of Your Only Move Is HUSTLE, for a
Lean proof of the core game. See
`docs/labbook/2026-11-13-core-game-scope-five-characters.md`, decisions 4
and 5.

Modules: `fixed`, the fixed-point number type; `godot_hash`, Godot
3.5.1's `hash()` builtin; `rng`, the logic RNG (Godot's PCG32 plus
`BetterRng`'s `choose` and `weighted_choice`); `physics`, the native
`FGObject` class's object physics (gravity, friction, forces,
movement, pushback); `tables`, the five official characters' and
their projectiles' data (see "`tables`" below); `fixed_text`,
`&str`/`String` wrappers over `fixed`'s byte-based API, behind the
`text` feature (off by default; see "The core and the shell" below).

## Why no dependencies

The game's native library, `lib/tbfg.so`, computes with `fixed::types::I48F16`
(the `fixed` crate, version 1.19.0), plus `fixed-sqrt` 0.2.5 for square root
and an undisclosed `fixed_trigonometry` 0.4.4 for sine, cosine, and `atan2`.
`tools/fixedmath_diff/rust` links those three crates directly, for a
reference check.

This crate does not link them. Charon, the tool that extracts a Rust
function's MIR for the Aeneas/Lean pipeline, must see every function a Lean
proof reasons about. A crate with no dependencies gives Charon a closed,
small surface. So `fixed.rs` copies the three crates' algorithms, bit for
bit, read from their own published source. Each function names the function
it copies.

## The core and the shell

This crate follows the lab book's functional-core/imperative-shell rule
(decision 9) at the module level, not only the function level: `fixed.rs`,
`godot_hash.rs`, and `rng.rs` are the proved core. None of the three ever
builds or reads a `String` or `&str`. `fixed::Fixed` parses from a byte
slice (`from_bytes`) and formats into a fixed-size byte array
(`display_bytes`), not a string. `godot_hash::hash_bytes` folds over
`&[u8]`.

`fixed_text.rs` is the shell: thin `&str`/`String` wrappers
(`fixed_text::from_str`, `fixed_text::display`) that callers outside the
Lean-checked core want (`tools/fixedmath_diff`, which formats JSON).
It is behind a Cargo feature, `text`, off by default. Charon runs a plain
`cargo build` with no features, so it never sees `fixed_text.rs`,
`String`, `&str`, or a `str::as_bytes` call anywhere in the crate: the
file does not exist in that build at all, not merely unreached code.
Callers that want the shell enable the feature in their own `Cargo.toml`
(`hustle_core = { path = "...", features = ["text"] }`).

One core function crosses the line on purpose:
`fixed::Fixed::hash_display` computes Godot's `hash()` of a `Fixed`
value's own canonical display text, with no `String` anywhere: it reads
`display_bytes`'s output (a byte array plus a length) and folds
`godot_hash::hash_bytes_prefix` over it directly.
`characters/BaseChar.gd`'s `get_state_hash()` needs exactly this: it
hashes six `FixedMath` display strings (position, velocity, and DI) and
one state name, every tick for the logic RNG's reseed. `tools/rng_diff`
checks it against the game (`hash_fixed_display` probes, 15/15 matched,
2026-11-14).

`godot_hash::hash_bytes` treats every byte as one Unicode code point,
which is exactly right for ASCII input and wrong for anything else. Every
string the game logic hashes is ASCII: a `FixedMath` display string
(digits, `-`, `.`), and a state name (a Godot node's `.name`, set by each
state node's name in its character's `.tscn` scene — checked: all 293
direct children of a `StateMachine` node, across the five official
characters' `.tscn` files, are ASCII, 2026-11-14).

## The Rust subset

This crate follows decision 5's subset:

- plain structs (`Fixed(pub i64)`, `Rng`, `ScanState`) and enums
  (`TieResult`, `FracCmp`), no trait objects, no `unsafe`, no hash maps,
  no const generics (two call sites that could have used one were split
  into concrete functions instead, since this was not checked against
  Aeneas).
- no `while` or `for` loop whose body uses `?`, `return`, `break`, or
  `continue`. Every such loop is recursion over an explicit index instead
  (string scanning, digit buffers, carry propagation, angle reduction,
  the RNG's weighted-choice scan).
- every arithmetic method calls an explicit `wrapping_*` or `checked_*`
  function, never a bare operator, so its behaviour on overflow does not
  depend on the crate's own build profile.
- errors are `Option` values, not panics. A parse failure, a division by
  zero, or a square root of a negative number returns `None`, the same
  outcome the game sees as Nil through `gdnative-core`'s panic boundary.
- a comparison that must become a plain `Bool` value (stored, returned,
  or passed as an argument) is always written as its own direct `if`,
  never combined with `||`/`&&` and stored to a `let` first. Aeneas
  elaborated an order comparison (`>`, `<`, `>=`, `<=`) combined this way
  as a Lean `Prop`, not `Bool`, and then failed to synthesize `Decidable`
  for it wherever the stored result was reused (`lake build`,
  2026-11-14). A comparison used directly as an `if` condition did not
  have this problem anywhere in this crate. Every place this crate once
  did otherwise is now two or three plain `if`s instead, each ending in
  a literal `true`/`false` or an early `return`.
- no Rust identifier is named the same as its own module. A parameter
  named `rng` inside `rng.rs` (the `rng` module) made Aeneas's generated
  Lean try to resolve `rng.field` as a namespace-qualified lookup instead
  of a structure projection, failing with "the environment does not
  contain `hustle_core.rng.Rng.Rng`" (`lake build`, 2026-11-14). The
  parameter is named `r` instead; the module and the type keep their own
  names.

## Axioms

None. `tools/lean_check`'s pipeline (see below) produced no
`FunsExternal.lean`/`TypesExternal.lean` for this crate: every function
and type translated to a `def`/`structure`/`inductive`, with nothing
left to an `axiom`. This took removing five primitive-method calls Aeneas
cannot extract MIR for (no Rust source exists for them to extract):

- `i64::wrapping_div`, replaced by a two-line function using `==`, `/`
  and the `i64::MIN` constant (`wrapping_div_i64` in `fixed.rs`).
- `i64::abs`, replaced by a comparison and unary `-` (`Fixed::abs`).
- `i128::unsigned_abs`, replaced by the literal constant it would always
  compute (`I64_MIN_MAGNITUDE` in `fixed.rs`).
- `u16::wrapping_neg`, replaced by subtraction in a wider `u32` plus a
  mask (`wrapping_neg_u16` in `fixed.rs`).
- `u32::wrapping_rem`, replaced by `a - (a / b) * b` (`wrapping_rem_u32`
  in `rng.rs`).

And one standard-library trait impl: comparing two `core::cmp::Ordering`
values with `==` made Aeneas axiomatize `core`'s own derived `PartialEq`
impl for it (no Rust-source MIR Charon can reach for a dependency's own
code). `fixed.rs` defines its own 3-variant `FracCmp` enum instead,
derives `PartialEq` on it directly (so the derive expands inside this
crate, where Charon already extracts everything), and never touches
`core::cmp::Ordering`.

The `?` operator (`Option`'s `Try` impl) is also gone from the core:
every function that used it is split so the `?` becomes a plain `match`
in a non-loop function body (allowed; only `while`/`for` loop bodies
must avoid it), which does not need an axiom for `Try::branch` either.

`tables` (see "`tables`: the five characters' data" below) is a
partial exception to "none" at the Charon level, not the Lean level.
Charon marks every table data function and large const in `tables/`
opaque (`tools/tablegen/gen.py`'s `emit_opaque_patterns`; see
`tools/lean_check/README.md`, "port/hustle_core, the tables slice" --
Aeneas's translation of this module did not finish in over ten
minutes otherwise). Marking an item opaque makes Aeneas emit one
`axiom` per item, the same template it would emit for any opaque
call. This crate does not keep that template: `gen.py` writes a real
Lean `def` for every one of those items instead, from the same `raw`
data its own Rust emitter reads, so the crate's Lean output still
carries zero axioms end to end.

## How it is checked

- `tools/fixedmath_diff` runs the game's own `FixedMath` methods and two
  Rust reimplementations (the `fixed`/`fixed-sqrt`/`fixed_trigonometry`
  crates, and this crate, through the `text` feature) on the same probe
  set, and compares all three line by line: 32,529/32,529 calls agree
  between this crate and the crate reference. See
  `tools/fixedmath_diff/README.md`.
- `tools/rng_diff` runs the game's `hash()` builtin and logic RNG against
  `godot_hash.rs` and `rng.rs`, including the `hash_fixed_display` probes
  against `fixed::Fixed::hash_display`: 160/160 records matched. See
  `tools/rng_diff/README.md`.
- `tools/fgobject_diff` runs the game's native `FGObject` object physics
  against `physics.rs` on a large edge-value probe set and on six real
  matches played through the live path: as of this crate's last
  completed run, 1,356,455/1,377,456 calls matched (probe plus six
  live replays), with 33 objects' first mismatching call left open
  across 4 methods. See `tools/fgobject_diff/README.md`, "Live stage",
  for the exact count, the six decompile-derived fixes this task made,
  and what is not yet done.
- `tools/lean_check` runs Charon (MIR extraction) and Aeneas (LLBC → Lean
  translation) on this crate, then `lake build` on the result
  (`HustleCorePort` in `tools/lean_check/generated/`): 0 Aeneas errors,
  0 axioms, build green. See `tools/lean_check/README.md`,
  "port/hustle_core" and "port/hustle_core, the tables slice".
- `tools/tablegen` checks `tables/` against a headless Godot probe of
  the real `.tscn` scenes, field by field, state by state, hitbox by
  hitbox. See `tools/tablegen/README.md`.

## `tables`: the five characters' data

`tables/` holds the five official characters' and their projectiles'
data: `common.rs` (shared enums: cancel categories, stances, hit
heights, and similar), `base.rs` (the 58 states `characters/BaseChar.tscn`
defines, unmodified), one module per character (`ninja.rs`, `cowboy.rs`,
`wizard.rs`, `robot.rs`, `mutant.rs`), and `projectiles/` (one module per
projectile scene a character's states reference, found by a scan, not a
fixed list).

`tools/tablegen` generates this directory from the game's own `.tscn`
and `.gd` files; see its own README.md for the schema, the kept and
dropped field lists, and the two data anomalies it found and kept as
explicit data rather than guessing at a fix. This is data only: the
`_tick`/`_frame_N` behavior that reads it is a later slice (decision 9).

A table's own `enum State`, `struct StateData`, `struct HitboxRecord`
and `struct ...RootConstants` all follow this crate's Rust subset the
same way `fixed.rs` and `rng.rs` do: plain, monomorphic structs and
enums, no generics, no hash maps, a `fixed` field typed `Option<Fixed>`
(never a bare `Fixed`), so the two real anomalies in the game's own data
(a hitbox field holding a non-decimal string) store an explicit `None`
instead of a panic or a guessed number.
