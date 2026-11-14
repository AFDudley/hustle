# RNG / hash diff

This tool checks one slice of the Rust port of Your Only Move Is HUSTLE: the
game's logic RNG (`framework/BetterRng.gd`, a Godot `RandomNumberGenerator`,
which is a PCG32 generator) and Godot 3.5.1's `hash()` builtin, which seeds
it. See `docs/labbook/2026-11-13-core-game-scope-five-characters.md`,
decisions 4 and 5.

The method: run the same probes in the real engine (Godot, headless) and in
`port/hustle_core`'s `rng.rs` and `godot_hash.rs`. Compare the two outputs
line by line.

## The result

**160/160 probe records matched exactly. 0 mismatches.**

| Record kind | Records | Matched |
|---|---:|---:|
| `hash_int` | 34 | 34 |
| `hash_string` | 25 | 25 |
| `hash_fixed_display` | 15 | 15 |
| `hash_rng` | 35 | 35 |
| `hash_static_rng` | 35 | 35 |
| `seq` | 16 | 16 |

A `seq` record checks a full run of logic-RNG calls after one `seed =` or
`state =` assignment, not one call. The 16 `seq` records hold 304 individual
RNG operation calls in total (96 `randi_range`, 80 `choose`, 64 `randi`, 64
`weighted_choice`), and every one of the 16 records' result lists matched
the game's, so all 304 calls matched too.

`report.json` (written by `compare.py`) holds the same counts, plus every
mismatch in full, grouped by record kind. There are none to show.

## What each record kind checks

- **`hash_int`**: Godot's `hash()` builtin on an `int`. Probe set: the
  edges `0`, `±1`, `±2`, `i64::MIN`, `i64::MAX`, the `u32` boundary
  (`4294967295`, `4294967296`, `-4294967296`), the `i32` boundary, and 20
  seeded-random values.
- **`hash_string`**: `hash()` on a `String`. Probe set: real state names
  from the game's GDScript (`Grabbed`, `HurtDizzy`,
  `WhiffInstantCancel`, `Burst`, `DefensiveBurst`, `OffensiveBurst`,
  `Idle`, `NewParry`), fixed-point strings (`"-12.34567"`, the type's
  integer-range boundary, 1 ulp, etc.; see `tools/fixedmath_diff` for
  where these strings come from), and plain ASCII strings (empty, a
  space, punctuation).
- **`hash_fixed_display`**: `hash()` on a canonical `FixedMath` display
  string (`"0.5"`, `"-140737488355328"`, etc.; see `RngDiff.gd`,
  `CANONICAL_FIXED_STRINGS`). Checked against
  `hustle_core::fixed::Fixed::hash_display`, a core function that hashes
  the value's own display digits straight from a byte array, with no
  `String` (the `fixed`/`display` pair in `fixed_text.rs` is shell
  code, excluded from the Lean build; see `port/hustle_core/README.md`).
- **`hash_rng`**: `obj/BaseObj.gd`'s `hash_rng()` reseed formula in full:
  `logic_rng.seed = hash(logic_rng.state + hash(fighter_hash) +
  hash(opponent_hash))`, for 7 states crossed with 5 `(fighter_hash,
  opponent_hash)` pairs, including `i64::MIN`/`i64::MAX`.
- **`hash_static_rng`**: the same formula with one hashed input
  (`hash(game_tick)`) instead of two, for 7 states crossed with 5 ticks.
- **`seq`**: a fixed sequence of 19 logic-RNG calls, run once per seed (9
  seeds) and once per state (7 states): `randi()` (4 times, interspersed),
  `randi_range` with `from == to`, `from > to` (twice, one each sign), and
  a wide but overflow-safe range (6 calls total), `choose` on array
  lengths 1 through 5 (the length-1 case always reads index `-1`), and
  `weighted_choice` on 4 different weight lists, one of which has interior
  zero weights.

## A native crash, not a port bug

`randi_range(i32::MIN, i32::MAX)` crashes the real engine with `SIGFPE`.
Found while building this tool's probe set; reproduced once in isolation.

`core/math/random_number_generator.h`'s `randi_range(int from, int to)`
computes `to - from + 1` in a 32-bit `int` before the modulo. For
`from = -2147483648, to = 2147483647`, the true difference is
`4294967295`, which overflows a 32-bit signed `int` (undefined behaviour;
in this build it wraps to `-1`), so the divisor becomes `-1 + 1 = 0`. The
native method then computes `ret % 0`, an integer division by zero, which
raises `SIGFPE` and kills the Godot process outright — not a GDScript
script error, not a caught exception.

The port's `randi_range` (`port/hustle_core/src/rng.rs`) does the same
32-bit arithmetic with `wrapping_*` operations, so it reproduces the same
divisor-becomes-zero computation without crashing — Rust's `wrapping_rem`
would panic on an actual zero divisor rather than silently matching a
crash, so this one combination is intentionally excluded from the `seq`
probe (see the comment at that call site in `RngDiff.gd`). No real game
call site passes a range this wide; `randi_range`'s legal call sites in
`obj/BaseObj.gd` and `framework/BetterRng.gd` all use small, bounded
ranges.

## How it works

- `RngDiff.gd` + `RngDiff.tscn`: a headless Godot scene. It calls `hash()`
  directly on a probe set of ints and strings, runs the `hash_rng()` /
  `hash_static_rng()` reseed formulas on a `BetterRng` instance, and runs
  fixed sequences of RNG calls after setting `.seed` or `.state`. It
  writes one JSON line per probe to `--rngd-out=<path>`.
- `rust/`: a crate that depends on `hustle_core` by path (staged next to
  it by `build.sh`; see "The container" below). It reads that file,
  ignores `"result"` (the game's own answer), recomputes with
  `hustle_core::godot_hash` and `hustle_core::rng`, and writes a line with
  the same shape.
- `compare.py`: lines the two files up by position (both iterate the same
  probe list, in the same order), checks every field but `"result"` is
  equal (so the two sides agree on which probe ran), then checks
  `"result"`.
- `container.sh`: the host-side entry point. Three podman stages, each
  read-only on the repository: Godot (reusing `tools/replay_check`'s Nix
  image and `build.sh`), Rust (this tool's own Nix image, built by its
  own `build.sh`), and the comparison (the same Nix image again, for its
  Python 3). No stage runs Godot, `cargo`, or this task's Python directly
  on the host.

```sh
tools/rng_diff/container.sh
cat ~/.cache/hustle-rng-diff/out/report.json
```

## The container

`flake.nix` (with `nix/rust.nix` and `nix/image.nix`) builds
`localhost/hustle-rng-diff` with Nix, the same way
`tools/fixedmath_diff/flake.nix` builds its own image. `build.sh` builds
it inside the pinned `docker.io/nixos/nix` container
`tools/nix_image_build.sh` uses for every tool, and loads it into podman.

This tool's Rust crate (`rust/Cargo.toml`) has a path dependency on
`hustle_core` (`port/hustle_core`), which lives outside this directory and
is owned by a different task slice of the same port. `tools/nix_image_build.sh`
gained an optional `EXTRA_SRC:EXTRA_DEST` argument for this: `build.sh`
passes `port/hustle_core:hustle_core`, which stages that directory's
git-tracked files as a sibling of this tool's own `rust/` directory inside
the Nix flake source, so `rust/Cargo.toml`'s `path = "../hustle_core"`
resolves. `nix/rust.nix` sets `cargoRoot = "rust"` and
`buildAndTestSubdir = "rust"` so `buildRustPackage` finds `Cargo.lock` and
builds the right crate out of that two-directory source tree.

The image holds two things:

- **The `rng_diff` binary**, built by `nix/rust.nix` with
  `rustPlatform.buildRustPackage` straight from `rust/Cargo.lock`.
  `hustle_core` has no dependency of its own; `serde_json` and its
  dependencies come from crates.io, checked against the lockfile.
- **Python 3**, for `compare.py`.

## Limits

- The probe set is large enough to exercise every code path in `rng.rs`
  and `godot_hash.rs` (both reseed formulas, both hash types, every
  `randi_range` branch, every `choose` array length from 1 to 5, a
  `weighted_choice` with a zero weight) but is still finite. A value
  outside it could still diverge.
- `randi_range(i32::MIN, i32::MAX)` is excluded from the probe set because
  it crashes the real engine (see above), not because the port disagrees
  with it.
