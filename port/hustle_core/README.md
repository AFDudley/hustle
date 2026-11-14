# hustle_core

This crate is the start of a Rust port of Your Only Move Is HUSTLE, for a
Lean proof of the core game. See
`docs/labbook/2026-11-13-core-game-scope-five-characters.md`, decisions 4
and 5.

This slice holds one module: `fixed`, the game's fixed-point number type.

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

## The Rust subset

`fixed.rs` follows decision 5's subset:

- plain structs (`Fixed(pub i64)`), no trait objects, no `unsafe`, no hash
  maps.
- no `while` or `for` loop whose body uses `?`, `return`, `break`, or
  `continue`. Every such loop is recursion over an explicit index instead
  (string scanning, digit buffers, carry propagation, angle reduction).
- every arithmetic method calls an explicit `wrapping_*` or `checked_*`
  function, never a bare operator, so its behaviour on overflow does not
  depend on the crate's own build profile.
- errors are `Option` values, not panics. A parse failure, a division by
  zero, or a square root of a negative number returns `None`, the same
  outcome the game sees as Nil through `gdnative-core`'s panic boundary.

## How it is checked

`tools/fixedmath_diff` runs the game's own `FixedMath` methods and two Rust
reimplementations (the `fixed`/`fixed-sqrt`/`fixed_trigonometry` crates, and
this crate) on the same probe set, and compares all three line by line. See
`tools/fixedmath_diff/README.md`.
