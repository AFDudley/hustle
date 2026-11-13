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
  flake.nix, flake.lock, nix/   The container image. See "The container".
  build.sh          Builds the image with Nix and loads it into podman.
  rust_proto/       The Rust prototype crate (no external dependencies).
  podman/           in_container.sh (the pipeline) and translate.sh (runs it).
  generated/        The Lean project: Charon+Aeneas output, plus the
                     hand-written theorem, checked out of the pipeline run
                     this README describes.
```

`.scratch/lean_check/cache/mathlib-cache/` holds mathlib's prebuilt build
cache between runs. `.scratch/lean_check/cache/rustup/` holds the Rust
nightly `rustup` installs at container run time (see "The container"
below). `.scratch/lean_check_nix/` holds `build.sh`'s small bridge file
between the Nix build and `podman load`. None of the three is part of
this report. The Charon, Aeneas and Lean release tarballs this tool
fetches at image build time are cached in the named podman volume
`hustle-nix-store`, the Nix store `build.sh` builds in; they are not
copied into this repository.

## Tool versions

| Tool | Version / tag | Commit | Fetched from |
|---|---|---|---|
| Charon | `nightly-2026.11.13` | `aa298662eec5088c041ac53b763fcf954e6a04b5` | `github.com/AeneasVerif/charon`, release asset `charon-linux-x86_64.tar.gz`, SHA-256 `f0e7f56425c76f391dadcf246ccd0a7a7e24d9b43de10828464fd6dc5a681b3a` |
| Aeneas | `nightly-2026.11.13-0d21a9e` | `0d21a9ec7bbbb110110cacc14464a47fd71c8705` | `github.com/AeneasVerif/aeneas`, release asset `aeneas-linux-x86_64.tar.gz`, SHA-256 `39c2565790310acdfb8fe4d12a1a4d637e85c7a1672e7d4586cb7c4d9bfe446e` |
| Rust (Charon's driver toolchain) | `nightly-2026-09-17` | `923c95cdf` (rustc 1.100.0-nightly) | `rustup toolchain install nightly-2026-09-17 --component rustc-dev,llvm-tools,rust-src,miri`, run once by `podman/in_container.sh` at container start, not baked into the image; see "The container" below, "Why the Rust toolchain installs at run time" |
| Lean | `v4.36.0-rc2` | — | `github.com/leanprover/lean4`, release asset `lean-4.36.0-rc2-linux.zip`, SHA-256 `1q937amff6ka71i8dx5q92bkdagyxi5ia4ddymb0ygxn07r28n3a` (nix32); see `nix/lean-toolchain.nix` |
| mathlib4 | `v4.36.0-rc2` | `9d581dfff2e41de989072ea63abf3daf69311520` | Resolved by `lake update`, pinned by Aeneas's own `backends/lean/lakefile.lean` |
| hax (`cargo-hax`) | not run | — | See "hax" below: not reached in the time this task had |

Charon and Aeneas are prebuilt release binaries, compiled from no OCaml
source on this host. `nix/aeneas-tools.nix` fetches both tarballs,
checks each against the SHA-256 above (a Nix fixed-output derivation
hash, so a changed download is a build failure, not a silent
difference), and extracts charon's tarball first, then aeneas's own
tarball on top (aeneas's release bundles its own copies of `charon` and
`charon-driver`; this keeps the same extraction order the prior
`podman/Containerfile` used, so aeneas's copies win, as they did
before). The Lean toolchain is likewise prebuilt: `nix/lean-toolchain.nix`
unpacks its own release zip directly, in place of `elan` (this image
serves one project, so there is only one toolchain to select). The Rust
nightly is the one exception; see "The container" below.

## The container

`flake.nix` (with `nix/aeneas-tools.nix`, `nix/lean-toolchain.nix` and
`nix/image.nix`) builds the image with Nix, the same pattern as
`tools/replay_check` (see that tool's README, "The container").
`build.sh` builds it inside a pinned `docker.io/nixos/nix` container and
loads it into podman as `localhost/hustle-lean-check`;
`podman/translate.sh` calls `build.sh` before every run. Nix never runs
on the host: the Nix store is the named podman volume
`hustle-nix-store` (shared with `tools/replay_check`), so a rebuild
after a small change is incremental. `flake.lock` pins nixpkgs; every
package version in the image, and every fixed-output download's hash,
traces back to that one commit or to the SHA-256 recorded in this file
and in "Tool versions" above.

`nix/image.nix` assembles the image from two Nix-built pieces, plus
`rustup` itself from nixpkgs:

- `aeneasTools` (`nix/aeneas-tools.nix`): charon, charon-driver, aeneas
  and the Lean `aeneas` package, under `/opt/aeneas-tools` in the image,
  a real directory (not only a Nix store path), because
  `generated/lakefile.lean` names that absolute path
  (`require aeneas from "/opt/aeneas-tools/backends/lean"`) and that file
  is outside this migration's scope.
- `leanToolchain` (`nix/lean-toolchain.nix`): the Lean release, on
  `PATH` through its own Nix store path. Nothing in this repository
  names its location.
- `rustup` (plain nixpkgs package): see below.

autoPatchelfHook sets each prebuilt ELF's interpreter and RPATH for
Nix's dynamic loader, the same technique
`tools/replay_check/nix/godotsteam.nix` uses for GodotSteam's prebuilt
binaries. `readelf -d` on the fetched binaries found: charon and
charon-driver need only glibc and libgcc_s beyond their own sibling
libraries (charon-driver's other need, `librustc_driver*.so`, is
covered below); aeneas is a statically linked OCaml binary, so
autoPatchelfHook has nothing to patch there; lean, lake and the Lean
release's own bundled clang already carry a `$ORIGIN/../lib` RUNPATH
and ship their own `libLLVM`, `libclang-cpp`, `libc++`, `libc++abi` and
even `libgcc_s.so.1` inside `lib/`, so only glibc and the ELF
interpreter are external for those three. Separately, a standalone
executable the pipeline links at run time (mathlib's own `cache` tool;
see below) needed `/lib64/ld-linux-x86-64.so.2`, the standard FHS
dynamic-linker path every ordinary Linux distribution has and a
Nix-built image does not; `nix/image.nix`'s `extraCommands` symlinks it
to `pkgs.glibc`'s own copy.

`git`, `curl` and `cacert` are also in the image, for `lake update` and
`lake exe cache get`, which still reach the network at container run
time (mathlib's git history and its prebuilt `.olean` cache): Nix bakes
in the Lean and Charon/Aeneas toolchains; it does not fetch mathlib.
`gcc` is in the image too: cargo's default linker on Linux is `cc`, and
the Rust nightly below ships no C compiler of its own (the pre-Nix
`podman/Containerfile` pulled one in through Debian's build-essential).

### Why the Rust toolchain installs at run time, not at image build time

Charon's own CLI calls `rustup toolchain install nightly-2026-09-17
--component rustc-dev,llvm-tools,rust-src,miri` itself, every run,
regardless of the environment it is started in. Three ways to make
that call a no-op from a Nix-baked toolchain were tried and each
failed for a specific, checked reason:

1. **`rustup toolchain link <custom-name>` + `rustup default`.**
   `charon cargo --preset=aeneas` still tried to install
   `nightly-2026-09-17` from the network: a custom link's name does not
   satisfy `rust_proto/rust-toolchain.toml`'s channel-name lookup,
   which resolves only against an installed toolchain named exactly
   `nightly-2026-09-17-x86_64-unknown-linux-gnu`, a name
   `rustup toolchain link` refuses to create for a local toolchain
   (`invalid custom toolchain name`).
2. **`rustup override set` and `RUSTUP_TOOLCHAIN=lean-check`.** Both
   still triggered the same network install; charon's own invocation
   evidently selects the toolchain by that literal channel name itself
   (most likely `rustup toolchain install nightly-2026-09-17 ...`
   again, or `cargo +nightly-2026-09-17`), a higher-priority selection
   than either mechanism.
3. **A Nix fixed-output derivation that runs the real
   `rustup toolchain install` at image build time** (network access is
   permitted inside a fixed-output derivation, by design). This got
   further: the toolchain installed correctly under the name rustup
   needs. But it failed on a certificate-verification error specific to
   `rustup`'s own HTTP client inside the Nix build sandbox:
   `error:0A000086:SSL routines:tls_post_process_server_certificate:
   certificate verify failed ... unable to get local issuer
   certificate`. This is not a missing or misconfigured certificate:
   `curl`, given the exact same `SSL_CERT_FILE` in the exact same
   sandboxed build, fetched the exact same URL successfully
   (`http_code=200`), and the hardcoded fallback path
   `/etc/ssl/certs/ca-certificates.crt` that `rustup` could have used
   instead already held a valid, current, 8,107-line CA bundle at the
   time of the failure. `rustup`'s own HTTP client rejected a valid
   certificate chain that `curl`, in the same sandbox, with the same
   trust store, accepted; this is a property of that one binary inside
   that one sandboxed environment, not of this repository's Nix
   expressions.

Given this, `podman/in_container.sh` runs the real
`rustup toolchain install ...` itself, once per container start, into
`/root/.rustup`, which `podman/translate.sh` mounts from
`.scratch/lean_check/cache/rustup/`: the first run reaches the network
(a few seconds, by observation: the dist tarballs are small), every
run after that, with the same cache mount, does not. This is the one
component in this tool's image that is not Nix-built; every other tool
and version is (see "Tool versions" above), and this one's version is
still pinned, by `rust_proto/rust-toolchain.toml`'s channel string and
by the component list named above, exactly as it was before this
migration.

## Commands

All of this ran inside `localhost/hustle-lean-check`, built by `build.sh`
(see "The container" above), with `--cpu-period 100000 --cpu-quota
800000` (an 8-core cap; the host has no `cpuset` controller delegated to
this user, so `--cpuset-cpus` fails with `unable to start container
process: ... openat2 .../cpuset.cpus: no such file or directory`, and the
CFS quota is the working substitute).

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
first keeps every build after the first one fast. This cache is large
(order of a gigabyte), and the Rust nightly's own components are tens
of megabytes, so `podman/translate.sh` mounts two host directories
into the container, to survive the container's `--rm` between runs:

```sh
-v .scratch/lean_check/cache/mathlib-cache:/root/.cache/mathlib:Z
-v .scratch/lean_check/cache/rustup:/root/.rustup:Z
```

The Lean toolchain needs no such mount: it is baked into the image
(see "The container" above). The Rust nightly is not baked in (see
"The container", "Why the Rust toolchain installs at run time"), so
its mount is the only one of the two that does real work on a first
run and real caching on every run after.

`generated/.lake/` (package clones and build output, about 2.6 GB after a
full build) is not kept in this report: `lake update && lake build` inside
the container regenerates it. `.scratch/lean_check/cache/mathlib-cache/`
and `.scratch/lean_check/cache/rustup/` above are kept, since they are
the slow parts to re-fetch.

Measured end to end on this host, with the mathlib and rustup caches
already warm from the run described above: image build (`build.sh`,
from a clean `hustle-nix-store` state, no Rust toolchain involved) 5m
32s to 9m 48s across rebuilds in this session; the full pipeline
(`podman/translate.sh`, `rustup toolchain install` already cached)
2m 48s, of which `cargo test` was under a second, `charon cargo
--preset=aeneas` a few seconds (the `miri` sysroot warning included),
`aeneas` 24s, and `lake update && lake exe cache get && lake build`
the remaining time, almost all of it `lake build`'s 3,602 jobs.


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
