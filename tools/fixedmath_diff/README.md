# FixedMath diff

This tool checks one risk for the Rust port of Your Only Move Is HUSTLE: does
`fixed 1.19.0` plus `fixed-sqrt 0.2.5` reproduce `lib/tbfg.so`'s `FixedMath`
exactly. See `docs/labbook/2026-11-13-core-game-scope-five-characters.md`,
open item 3.

The method: call every `FixedMath` method from GDScript on a large probe
set, and call the same methods in Rust on the same inputs. Compare the two
outputs line by line.

## The result

31,809 of 32,529 calls matched exactly (97.8%). Every live method (a method
the game calls from uncommented GDScript) matched on every probed input,
**except** `angle_dist` (6 mismatches of 1,098) and `sign` (1 mismatch of
214). Both exceptions are documented below. 0 live method showed a
systematic mismatch; every other live-method mismatch count is 0.

| Method | Live call sites in the game | Probe calls | Matched |
|---|---:|---:|---:|
| abs | 23 | 214 | 214 |
| add | 60 | 1,098 | 1,098 |
| angle_dist | 2 | 1,098 | 1,092 |
| angle_to_vec | 3 | 214 | 214 |
| ceil | 0 (dead) | 214 | 214 |
| deg2rad | 4 | 214 | 214 |
| div | 67 | 1,098 | 1,098 |
| eq | 31 | 1,098 | 1,098 |
| floor | 2 | 214 | 214 |
| ge | 11 | 1,098 | 1,098 |
| get_x_intercept | 0 (dead, commented) | 851 | 139 |
| gt | 79 | 1,098 | 1,098 |
| int_map | 0 (dead, commented) | 64 | 63 |
| le | 7 | 1,098 | 1,098 |
| lerp_angle | 1 | 837 | 837 |
| lerp_int | 0 (dead) | 1,372 | 1,372 |
| lerp_string | 24 | 837 | 837 |
| lt | 72 | 1,098 | 1,098 |
| mul | 382 | 1,098 | 1,098 |
| normalized_vec | 32 | 1,098 | 1,098 |
| normalized_vec_times | 69 | 837 | 837 |
| powu | 7 | 2,160 | 2,160 |
| rotate_vec | 12 | 837 | 837 |
| round | 150 | 214 | 214 |
| sign | 31 | 214 | 213 |
| sub | 49 | 1,098 | 1,098 |
| vec_add | 5 | 851 | 851 |
| vec_dist | 4 | 851 | 851 |
| vec_dist_squared | 0 (dead) | 851 | 851 |
| vec_div | 2 | 837 | 837 |
| vec_dot | 0 (dead) | 851 | 851 |
| vec_len | 50 | 1,098 | 1,098 |
| vec_len_squared | 0 (dead) | 1,098 | 1,098 |
| vec_mul | 48 | 837 | 837 |
| vec_round | 0 (dead) | 1,098 | 1,098 |
| vec_scale | 0 (dead) | 837 | 837 |
| vec_sub | 4 | 851 | 851 |
| vec_to_angle | 18 | 1,098 | 1,098 |

"Live call sites" counts an uncommented `fixed.<method>(` in the game's
GDScript (grep over `characters/`, `obj/`, `mechanics/`, `framework/`,
`game.gd`; `.scratch/` and `tools/` excluded). 9 of the 38 registered
methods are dead: the game never calls them, or calls them only in a
commented-out line.

## The fixed-point type

`FixedMath` uses `fixed::types::I48F16` (`FixedI64` with 16 fractional
bits): 64 bits total, 48 integer bits (signed), 16 fractional bits.

Found by calling `FixedMath` from GDScript with probing inputs (a
throwaway harness, since deleted; its findings are recorded here and in the
probe set below):

- **1 ulp is 1/65536 = 2^-16.** `add("0", "0.0000152587890625")` (exactly 1
  ulp) returns `"0.00002"`. This matches the labbook's finding that
  "the velocity keeps 1/65536-pixel precision" (section 2.2).
- **The integer range is -2^47 to 2^47 - 1.** `add("140737488355327", "0")`
  (2^47 - 1) returns the same string unchanged. `add("140737488355328",
  "0")` (2^47) returns Nil (a parse overflow). `add("-140737488355328",
  "0")` (-2^47) returns the same string unchanged; `-140737488355329`
  overflows.
- **64 bits total, confirmed by a repeated-doubling probe.**
  `powu("2", 47)` returns `"-140737488355328"` (wraps to the type's
  minimum: `2^47` at 16 fractional bits is raw bit `2^63`, which reinterprets
  as `i64::MIN`). `powu("2", 48)` returns `"0"` (raw bit `2^64 ≡ 0 mod
  2^64`). Both results need exactly 64 raw bits, no more and no fewer, to
  explain.

## The string rules

### Parsing (`FromStr`)

Found by probing `add(s, "0")` with many strings `s`, and by reading the
panic messages (`ParseFixedError` from `fixed-1.19.0/src/from_str.rs`,
reached through `gdnative-core`'s `catch_unwind` boundary, which turns a
Rust panic into a Nil Variant instead of crashing the process — see
"Panics are caught, not crashes" below).

| Input | Result | Reason |
|---|---|---|
| `"5"`, `"-5"`, `"+5"` | parses | one optional leading sign |
| `"5."`, `".5"` | parses, as `5` and `0.5` | a digit run on either side of the point is optional |
| `""` | Nil | `NoDigits` |
| `"abc"` | Nil | `InvalidDigit` |
| `"1e3"` | Nil | `InvalidDigit` — no exponent notation |
| `"1.2.3"` | Nil | `TooManyPoints` |
| `"--1"` | Nil | `InvalidDigit` — one sign only |
| `" 1"`, `"1 "` | Nil | `InvalidDigit` — no whitespace trimming |
| `"1_000"` | Nil | `InvalidDigit` — no digit-group underscore |
| `"140737488355328"` (2^47) | Nil | `Overflow` |

**Rounding at the 16th fractional bit is round-half-to-even**, not
round-half-up. `add("0", "0.0000076293945312")` (exactly half of 1 ulp)
returns `"0"` (rounds to the even neighbour, 0). `add("0",
"0.0000076293945313")` (one bit above half) returns `"0.00002"` (rounds up,
the only neighbour closer than the exact tie).

### Formatting (`Display`)

**At most 5 decimal digits, trailing zeros trimmed, no exponent.** 5 is
`⌈16 × log10(2)⌉`, the minimum digit count the `fixed` crate's `Display`
guarantees for a lossless round trip through `FromStr`, for a type with 16
fractional bits. `div("1", "3")` returns `"0.33333"` (5 digits, no
trimming: none are zero). `mul("1.5", "2")` returns `"3"` (not `"3.00000"`:
every fractional digit is zero, so the decimal point is dropped too).
`add("1.100", "0")` returns `"1.1"` (one non-zero digit, 4 trailing zeros
trimmed).

### Panics are caught, not crashes

`lib/tbfg.so`'s strings include `"gdnative-core: method call failed with
error: ... check module level documentation on gdnative::user_data"` and a
panic handler at `gdnative-core-0.11.3/src/private.rs:197`. A panic inside
a `FixedMath` method — a parse failure, `div("1", "0")` (a plain integer
"attempt to divide by zero", not a `ParseFixedError`), or `vec_len` on
operands whose squares overflow to a negative sum (`fixed-sqrt` "panics if
called on a negative signed number") — prints to stderr and returns Nil to
GDScript. The process keeps running: this harness made tens of thousands of
such calls in one process with no crash.

## Reconstructed, not read: 3 dead methods, and one quantified mismatch

`FixedMath` has 38 registered methods (`bin/FixedMath.gdns`,
`strings lib/tbfg.so`), not the task brief's 36. This is a plain recount,
unrelated to the undisclosed dependency below:
`strings lib/tbfg.so | grep -oE "FixedMath :: [a-zA-Z_]+" | sort -u` gives
38 distinct names. 9 of the 38 are dead (no uncommented call site). Of
those, 2 have a signature found only by trial and error, not read from
source, because every call site is commented out:

- **`get_x_intercept(x1, y1, x2, y2)`**: 712 of 851 probed calls mismatch.
  The only pattern this harness could confirm: when `y1 == y2` (a
  horizontal line), the result is the sentinel `"2147483647"` (`i32::MAX`
  read into the fixed type). The commented call site
  (`characters/stickman/states/QuickSlash.gd:57`) suggests an x-intercept
  of the line through `(x1,y1)`-`(x2,y2)` at `y = 0`, and that formula
  matches some probed cases (for example `(0,10)→(5,0)` gives `5`) but not
  others (for example `(0,5)→(10,15)` gives `10` in the game, not `-5`).
  **This mismatch is not resolved.** The method has no live call site, so
  it carries no risk for the port's `step` function; it is reported as an
  open question for anyone who later ports `QuickSlash.gd`'s dead code.
- **`lerp_int(a, b, max, f)`**: `a` and `b` are fixed-point strings; `max`
  and `f` are plain integers (found by passing wrong types and reading the
  `gdnative-core` argument-conversion errors, which name the expected type
  per argument). All 1,372 probed calls matched the reconstructed formula
  `a + (b - a) * (f / max)`, with `f / max` as a fixed-point fraction and
  floor division (see `int_map`, next) implied by the wrapping Fixed
  division. This is a successful reconstruction, not a mismatch.
- **`int_map(value, in_min, in_max, out_min, out_max)`**: all 5 arguments
  are plain integers; the formula is Arduino's `map()`:
  `out_min + (value - in_min) * (out_max - out_min) / (in_max - in_min)`,
  confirmed on 63 of 64 probed calls once the division was changed from
  Rust's truncating `/` to floor division (rounds toward negative
  infinity: `int_map(-76785, -100, 100, -50, 50)` needs floor division to
  get the game's `-38393`, not truncating division's `-38392`). The one
  remaining mismatch (`int_map(82939, -105, 105, -50, 55)`: game `41471`,
  port `41472`) is an **exact** division (no remainder), so it is not a
  rounding-direction difference; its cause is not resolved. The method has
  no live call site.

## An undisclosed dependency: `fixed_trigonometry`

The task's fixed facts name `fixed 1.19.0` and `fixed-sqrt 0.2.5` as
`lib/tbfg.so`'s crates. **A third crate exists:** `nm lib/tbfg.so | c++filt`
finds the local symbols `fixed_trigonometry::cos` and
`fixed_trigonometry::atan::atan2`, used by `angle_to_vec`, `rotate_vec`, and
`vec_to_angle`. No version pin could be recovered: `strings lib/tbfg.so`
shows a `/home/ivy/.cargo/registry/src/.../` path for every other
dependency (visible because each one panics somewhere, embedding its
source path), but none for `fixed_trigonometry` — its `cos`/`sin`/`atan2`
never panic, so the compiler never had to embed a path for it.

This crate's `cos`/`sin` are a Taylor approximation (its own docs say so),
so an un-pinned version is a real risk: a different `fixed_trigonometry`
release could compute a bit-different cosine. **Measured result: it is not
a risk in this probe set.** `tools/fixedmath_diff/rust` depends on
`fixed_trigonometry = "0.4.4"` (the crate's latest release as of this
report, not a confirmed pin), and every trig-dependent live method matched
100% of its probe calls: `angle_to_vec` 214/214, `rotate_vec` 837/837,
`vec_to_angle` 1,098/1,098. `angle_dist` and `lerp_angle` use the same `PI`
reconstruction (next section) layered on top of a plain subtract/multiply,
not `fixed_trigonometry` directly, and matched 1,092/1,098 and 837/837.

**Recommendation for the port:** pin `fixed_trigonometry` and re-run this
check before relying on it for the proof. A pin that happens to match
today is not a guarantee against a future `cargo update`.

## Two silent risks found in FixedMath itself

Both were invisible until the Rust side was built, run, and diffed against
the game — the stated goal of this task.

1. **`add`, `sub`, `mul`, `powu`, and vector sums wrap on overflow; they do
   not check it.** `mul("140737488355327", "2")` returns `"-2"`, not an
   error. A release build of the `fixed` crate's operators wraps instead of
   panicking (the panic-on-overflow check is a debug-only assertion), and
   `lib/tbfg.so` is a release build. The Rust port must call
   `wrapping_add`/`wrapping_sub`/`wrapping_mul` explicitly, never the bare
   `+`/`-`/`*` operators, or its behaviour on overflow will depend on its
   own build profile instead of matching the game unconditionally.
   `div` is the one exception among the arithmetic methods: it wraps on an
   overflowing quotient (`div("-140737488355328", "-1")` returns the same
   value unchanged, not an error) but still panics, caught as Nil, on a
   zero divisor. `vec_len`/`vec_dist`/`normalized_vec` divide by a vector
   length the same way.
2. **`round`, `ceil`, `floor`, `vec_round`, `vec_scale`, and `int_map`
   truncate their result to a 32-bit integer**, found from
   `ceil("140737488355327")` (the type's largest representable pure
   integer) returning `-1`: the low 32 bits of `2^47 - 1` (47 one-bits),
   read as a signed `i32`, are `0xFFFFFFFF`, i.e. `-1`. This never matters
   at game scale (positions and frame counts never approach `2^31`), but a
   Rust port that returns `i64` from these methods, matching their
   mathematical fixed-point result instead of this narrower contract,
   would diverge the first time a search or a bug produces a position
   outside `i32` range. The reconstruction in `rust/src/main.rs` applies
   this truncation explicitly (`wrap_i32`); without it, 445 of the 2,577
   probed calls to `ceil`, `floor`, `round`, `vec_round`, and `vec_scale`
   disagreed with the game. `sign` also returns an integer narrow enough
   that this truncation never changes its visible range (`-1`, `0`, `1`
   always fit in an `i32`); its one observed mismatch, below, has a
   different, unresolved cause.

## The comparison

- `angle_dist`, 6 of 1,098: a boundary tie at exactly `±π`. Example:
  `angle_dist("-3.14159265358979", "0")` (`a = -π`, `b = 0`, so the signed
  difference is exactly `+π`) returns `"-3.14159"` from the game and
  `"3.14159"` from the port. The underlying `wrap_phase` reduces an angle
  to a half-open range around `(-π, π]`; the probed data has the game
  wrapping `-π` up to `+π` in one direction (`angle_dist("0",
  "-3.14159265")` → `"3.14159"`) and `+π` down to `-π` in the other
  (this mismatch), which a single half-open interval cannot both satisfy at
  once with a reconstructed `π` constant. Not resolved; affects only
  inputs within roughly 1 ulp of exactly `±π`.
- `sign`, 1 of 214: `sign("-140737488355328")` (the type's minimum value)
  returns `"1"` from the game, `-1` from the port. A single extreme-value
  case; not resolved.
- `get_x_intercept`, `int_map`: see "Reconstructed, not read" above. Both
  are dead code.

Every other mismatch class from the first comparison pass (`div`, `ceil`,
`floor`, `round`, `sign`'s general case, `vec_round`, `vec_scale`,
`int_map`'s rounding direction, `normalized_vec`, `lerp_string`,
`lerp_int`) was root-caused and fixed in `rust/src/main.rs` during this
task; the counts above are the result after those fixes, not before.
`lerp_string`'s formula is `a * (1 - t) + b * t` (confirmed on 837/837
calls); the mathematically equivalent `a + (b - a) * t` matched only 787 of
837 — proof that fixed-point rounding is sensitive to the order of
operations, not just the formula.

## How it works

- `FixedMathDiff.gd` + `FixedMathDiff.tscn`: a headless Godot scene. It
  builds a probe set per method — edge values (`0`, `±1` ulp, the integer
  range boundary, a value one step past it, the round-half-to-even tie
  point), every clean numeric literal the game's GDScript passes to a
  `fixed.*` call (collected by grep, see the constant list in the script),
  seeded-random values (some with 5 decimals, matching the type's own
  display precision; some with 9, forcing `from_str` to round), and a
  handful of strings `FromStr` must reject — and calls `fixed.callv(method,
  args)` for each. It writes one JSON line per call to `--fmd-out=<path>`:
  `{"method", "args", "result", "error"}`.
- `rust/`: a crate (`fixed = "=1.19.0"`, `fixed-sqrt = "=0.2.5"`,
  `fixed_trigonometry = "0.4.4"`) that reads that file, ignores `"result"`
  and `"error"` (the game's own answer), recomputes with the same method
  and args, and writes a line with the same shape.
- `compare.py`: lines the two files up by position (both iterate the same
  input list, in the same order) and reports every `(method, args)` pair
  whose `error` flags disagree, or whose `result`s differ when neither
  errored.
- `container.sh`: the host-side entry point. Three podman stages, each
  read-only on the repository: Godot (reusing `tools/replay_check`'s image
  and `build.sh`), Rust (`docker.io/library/rust:1-slim-bookworm`, a public
  image), and the comparison (the same image as the Godot stage, for its
  Python 3). No stage runs Godot, `cargo`, or this task's Python directly
  on the host.

```sh
tools/fixedmath_diff/container.sh
cat ~/.cache/hustle-fixedmath-diff/out/report.json
```

## Limits

- The probe set is large (32,529 calls) but finite, and does not include
  every value in the type's ~2^64 range. A method that matches here could
  still diverge on an untested input.
- `get_x_intercept`, `int_map`'s one remaining case, `angle_dist`'s
  boundary tie, and `sign`'s extreme-value case are open questions, not
  closed ones. They are reported, not hidden, per this task's acceptance
  criteria.
- `fixed_trigonometry`'s version is unpinned (see above): today's match is
  not a permanent guarantee.
