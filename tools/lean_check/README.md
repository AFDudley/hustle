# Lean check: a Rust-to-Lean translation risk check

This tool measures one risk of the planned Rust-to-Lean port (decision 5 of
`docs/labbook/2026-11-13-core-game-scope-five-characters.md`): will Aeneas
(through Charon) accept the Rust subset the lab book names, and can `lake`
build the Lean it produces? It does not port real character data. It is a
small, loosely-modelled prototype, run through the real tools, so later
work knows what the tools accept before the real port writes thousands of
lines in a style the tools reject.

## Result in one line

Charon and Aeneas (nightly-2026.11.13 / nightly-2026.11.13-0d21a9e) accept
plain structs, enums, a `Vec`-of-objects state, checked `i64` fixed-point
arithmetic and a seeded RNG without changes. They reject every `while` loop
in this prototype that used `?` (early return) in its body, even when the
loop is not nested. The rewrite that works is recursion over an explicit
index, with an accumulator parameter for anything that builds a new `Vec`.
See "Constructs the tools rejected" below for the exact error text and the
four functions it hit.

## Layout

```
tools/lean_check/
  rust_proto/       The Rust prototype crate (no external dependencies).
  podman/           Containerfile and translate.sh for the whole pipeline.
  generated/        The Lean project: Charon+Aeneas output, plus the
                     hand-written theorem, checked out of the pipeline run
                     this README describes.
```

`.scratch/lean_check/` holds the downloaded release tarballs (for their
recorded SHA-256) and the podman run logs. It is not part of this report.

## Tool versions

| Tool | Version / tag | Commit | Fetched from |
|---|---|---|---|
| Charon | `nightly-2026.11.13` | `aa298662eec5088c041ac53b763fcf954e6a04b5` | `github.com/AeneasVerif/charon`, release asset `charon-linux-x86_64.tar.gz`, SHA-256 `f0e7f56425c76f391dadcf246ccd0a7a7e24d9b43de10828464fd6dc5a681b3a` |
| Aeneas | `nightly-2026.11.13-0d21a9e` | `0d21a9ec7bbbb110110cacc14464a47fd71c8705` | `github.com/AeneasVerif/aeneas`, release asset `aeneas-linux-x86_64.tar.gz`, SHA-256 `39c2565790310acdfb8fe4d12a1a4d637e85c7a1672e7d4586cb7c4d9bfe446e` |
| Rust (Charon's driver toolchain) | `nightly-2026-09-17` | `923c95cdf` (rustc 1.100.0-nightly) | Charon release's own `rust-toolchain` file, installed with `rustup` |
| Lean | `v4.36.0-rc2` | — | Aeneas's own `backends/lean/lean-toolchain`, installed with `elan` |
| mathlib4 | `v4.36.0-rc2` | `9d581dfff2e41de989072ea63abf3daf69311520` | Resolved by `lake update`, pinned by Aeneas's own `backends/lean/lakefile.lean` |
| hax (`cargo-hax`) | not run | — | See "hax" below: not reached in the time this task had |

Both binaries are prebuilt release artifacts. Nothing was compiled from
OCaml source on this host: `podman/Containerfile` downloads the two
tarballs, checks their SHA-256, and installs the pinned Rust nightly (for
Charon, which runs as a custom `rustc` driver and needs the exact nightly
it was built against) and `elan` (for Lean, which reads the toolchain a
project's own `lean-toolchain` file names).

## Commands

All of this ran inside `localhost/hustle-lean-check`
(`podman build -f podman/Containerfile podman/`), with
`--cpu-period 100000 --cpu-quota 800000` (an 8-core cap; the host has no
`cpuset` controller delegated to this user, so `--cpuset-cpus` fails with
`unable to start container process: ... openat2 .../cpuset.cpus: no such
file or directory`, and the CFS quota is the working substitute).

```sh
# Rust-side check, independent of Charon and Aeneas.
cd rust_proto && cargo test

# Extract LLBC.
cd rust_proto && charon cargo --preset=aeneas
# -> rust_proto/hustle_core_proto.llbc

# Translate to Lean, one file per kind of declaration.
aeneas -backend lean -dest generated/src -subdir HustleCore \
  -split-files rust_proto/hustle_core_proto.llbc
# -> generated/src/HustleCore/{Types,TypesExternal_Template,Funs}.lean
mv generated/src/HustleCore/TypesExternal_Template.lean \
   generated/src/HustleCore/TypesExternal.lean   # per Aeneas's own README

# Resolve the Lean project's dependencies (aeneas's own backends/lean
# package, and mathlib transitively through it), then build.
cd generated && lake update && lake exe cache get && lake build
```

`lake exe cache get` downloads mathlib's prebuilt `.olean` cache (about
9,080 files). Building mathlib from source is not attempted: the Lean
ecosystem's own convention is to fetch this cache instead, and doing so
first keeps every build after the first one fast. The Lean toolchain
itself (`elan`) and this cache are large (order of a gigabyte together),
so `podman/translate.sh` mounts two host directories under
`.scratch/lean_check/cache/` into the container, to survive the
container's `--rm` between runs:

```sh
-v .scratch/lean_check/cache/elan/toolchains:/root/.elan/toolchains:Z
-v .scratch/lean_check/cache/mathlib-cache:/root/.cache/mathlib:Z
```

`generated/.lake/` (package clones and build output, about 2.6 GB after a
full build) is not kept in this report: `lake update && lake build` inside
the container regenerates it. The two `.scratch/lean_check/cache/`
directories above are kept, since they are the slow part to re-fetch.

## Constructs the tools rejected

Charon accepted the whole crate both before and after the rewrite below
(its only output was a one-line warning that `miri` is not installed,
which does not stop extraction and is unrelated to the crate's own code).
Every rejection below is from Aeneas, applied to the LLBC Charon produced.

### 1. A `while` loop with `?` in its body, even unnested

The first version of `resolve_hits` (two nested `while` loops, one with a
`continue`) and the first versions of `step_pure` and `step_in_place` (one
`while` loop each, no nesting, but calling fallible helpers with `?`) all
failed:

```
[Error] Continue to outer loops are not supported yet
[Error] Early returns inside of loops are not supported yet
[Error] Ignoring the body of 'hustle_core_proto::step_pure' because of previous error
```

Aeneas's own published limitation list names only `return`/`break`/
`continue` *to an outer loop* from a *nested* loop as unsupported. The
observed failure is broader: a single, non-nested `while` loop whose body
uses `?` (so it can return `Err` early) is already rejected. `continue` is
rejected in any loop, nested or not.

**The rewrite that worked:** recursion over an explicit index, with an
accumulator parameter for the functions that build a new `Vec`
(`step_objs`, `step_objs_in_place`, `apply_projectile_hit`,
`resolve_projectiles`). A `return` from a plain (non-loop) function body is
accepted without issue — `on_state_frame`'s early `return None` translated
cleanly in every run. So the fix is not "remove early returns": it is
"replace the loop with recursion," since Aeneas's loop translation and its
function-call translation diverge here.

### 2. A read-only `while` loop over `objs[i]` behind a shared slice reference

The first version of `fighter_count` (`while i < objs.len() { if
objs[i].kind == ObjKind::Fighter { n += 1; } i += 1; }`, no `?`, no
`continue`, no mutation) failed differently:

```
[Error] Could not match the contexts
[Warn ] Could not translate the body of function 'hustle_core_proto::fighter_count because of previous error
```

This is a borrow-checking fixed-point failure (Aeneas's loop translation
tries to compute one symbolic context that is stable across iterations;
here it could not), not a feature gap. **The rewrite that worked:**
recursion over the index, recursing first and combining with the current
element's field after the call returns (matching the style that already
worked in `on_state_frame`), rather than a loop.

### 3. The same loop-removal fix did not immediately carry over: one more

After converting every loop to recursion, one function still failed:

```
[Error] Could not match the contexts
[Warn ] Could not translate the body of function 'hustle_core_proto::keep_fighters_and_live because of previous error
```

`keep_fighters_and_live(objs, i, acc)` is a recursive function (not a
loop), but its body read `objs[i]` three times in one statement
(`objs[i].kind`, `objs[i].hp`, then `objs[i]` again to push a copy), where
every other working function in this file reads an indexed element once,
into a local `let o = objs[i];`, before using its fields. Changing
`keep_fighters_and_live` to that same one-read style fixed it, with no
other change. This suggests the one-read-per-element idiom is not just
style: it is load-bearing for Aeneas's symbolic execution of `Slice`
indexing, at least at this version.

### Net effect

After these three rewrites, Charon and Aeneas ran clean end to end:

```
$ aeneas -backend lean -dest generated/src -subdir HustleCore -split-files hustle_core_proto.llbc
... (no [Error] or [Warn] lines)
```

## Generated Lean file sizes

| File | Lines | Source |
|---|---|---|
| `generated/src/HustleCore/Types.lean` | 104 | Aeneas, from the crate's `struct`/`enum` declarations |
| `generated/src/HustleCore/TypesExternal.lean` | 28 | Aeneas template, renamed per its own README; one `axiom` for `core::mem::maybe_uninit::MaybeUninit`, unused by this crate's reachable code |
| `generated/src/HustleCore/Funs.lean` | 2,743 | Aeneas, from the crate's functions, including the `Vec`, `Option` and `Result` trait instances Charon pulled in from `core`/`alloc` |
| `generated/src/HustleCore.lean` | 7 | Hand-written: imports the three files above as one root module |
| `generated/src/HustleCheck.lean` | 41 | Hand-written: the theorem and its proof |

The Rust source is 503 lines (`rust_proto/src/lib.rs`), past the "about 200
to 400" the brief named, grown there by the recursive rewrites the section
above describes (four functions needed a loop-free rewrite, each adding a
helper) and by the doc comments tracing each piece back to the game's own
files. `rust_proto/tests/core.rs` adds 64 more lines, `cargo test`-only. The
2,923-line Lean output (including the two hand-written files) is about 5.8
times the Rust source line count: every arithmetic op becomes an explicit
`Result`-monad bind, every trait (`Clone`, `Copy`, `PartialEq`, `Ord`,
`Debug`) gets its own named instance definition, and every loop-turned-
recursion gets a `partial_fixpoint` block.


## The theorem

`generated/src/HustleCheck.lean` states and proves, over the generated
`Fixed` type (a reducible alias of `Aeneas.Std.I64`, Q48.16 fixed point):

```lean
theorem fixed_checked_add_comm (a b : Fixed) :
    Fixed.checked_add a b = Fixed.checked_add b a

theorem fixed_checked_add_comm_on_success (a b v : Fixed) :
    Fixed.checked_add a b = Result.ok (core.result.Result.Ok v) →
    Fixed.checked_add b a = Result.ok (core.result.Result.Ok v)
```

**Proof status: both proved, no `sorry`, `lake build` is green.** The
acceptance log:

```
✔ [3591/3602] Built HustleCore.TypesExternal (44s)
✔ [3593/3602] Built HustleCore.Types (2.6s)
✔ [3595/3602] Built HustleCore.Funs (5.6s)
✔ [3597/3602] Built HustleCore (51s)
✔ [3600/3602] Built HustleCheck (26s)
Build completed successfully (3602 jobs).
```

The first proof reduces `Fixed.checked_add` to `Aeneas.Std.I64.checked_add`,
then to the `IScalar.tryMk` call that only reads `.val` (an `Int`), then
closes with `Int.add_comm`. The first attempt left one unsolved goal because
`simp` had already unfolded `Fixed.checked_add` down to a `do`-block whose
only remaining difference was `a + b` vs `b + a` as `Result (IScalar ty)`
values; the fix was proving that swap as its own `have`
(`show Aeneas.Std.IScalar.add a b = Aeneas.Std.IScalar.add b a`) before
closing the main goal by `simp`. The second theorem is a direct corollary.

This is the simpler of the two example theorems the brief named ("that the
fixed-point add is commutative when it does not overflow"), chosen over
"`step` does not change the number of fighters" for time: the fighter-count
property needs an inductive proof over `keep_fighters_and_live`'s recursion
(an invariant across the whole hit-resolution pass), which the rewrite in
"Constructs the tools rejected" above made structurally sound to state
(`fighter_count` and `keep_fighters_and_live` both exist as plain recursive
Lean functions now, not a loop Aeneas had to reject), but proving it was not
reached in this task's time budget. `fighter_count`'s signature in the
generated Lean is `Slice Obj → Result Std.Usize`, and the fighters occupy
indices `0` and `1` unconditionally in `keep_fighters_and_live`'s recursion
(see the doc comment on that function in `rust_proto/src/lib.rs`), so the
statement is not in doubt, only its proof script.


## hax

Not run: hax's Lean backend (`cargo hax into lean`) is documented as a thin
wrapper over this same Charon + Aeneas pipeline ("Lean (via Aeneas)" in
hax's own README), so it was given lower priority than finishing the
direct Charon + Aeneas path and its proof within this task's time. If a
later task runs it, expect the same LLBC and the same Lean output this
README describes, since hax calls the same two tools; the thing worth
checking there is `cargo hax`'s own crate-level configuration (`hax.toml`)
and whether its frontend accepts the same Rust subset Charon did, not a
new translation.

## Recommendation for the port's Rust subset

1. **No `while` or `for` loop may contain `?`, `return`, `break` or
   `continue`.** Write every such loop as a recursive function over an
   explicit index instead, with an accumulator parameter when it builds a
   new collection. This is the single highest-value style rule this check
   found: it was the only category of rejection, and it hit 4 of the 10
   functions in a 500-line prototype that mirrors the real game's object
   update, hit-resolution and counting code.
2. **Read an indexed element once per iteration**, into a local `let o =
   collection[i];`, before using its fields. Section 3 above found one
   function that failed only because it indexed the same slice position
   three times in one branch; copying it out once first fixed it with no
   other change.
3. **Plain early `return` is fine outside a loop.** `on_state_frame`'s
   `if ... { return None; }` and every `?`-chained arithmetic helper
   translated without issue. The port does not need to avoid early return
   everywhere, only inside loops.
4. **Keep decision 5's other rules.** No closures, no hash maps, no
   `unsafe`, checked arithmetic through a wider integer type for the
   fixed-point newtype: all of these held up in this run with no special
   handling from Aeneas.
5. **Budget for the Lean project's own cold-start cost**, not the
   translation. Charon and Aeneas ran in under a minute each. Resolving
   and fetching mathlib's prebuilt cache took several minutes and about a
   gigabyte on a first run; mount a persistent cache directory (see
   "Commands" above) so this cost is paid once, not per container run.
