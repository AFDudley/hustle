# Ghidra headless decompiler

A reusable tool: runs Ghidra's headless analyzer (`analyzeHeadless`) against
a binary and decompiles named functions or addresses to C, for tasks that
need to read a release build's actual logic instead of guessing from
disassembly or black-box probing alone.

Built for `tools/fgobject_diff`'s reconstruction of `lib/tbfg.so`'s
`FGObject` methods (see that tool's README.md), but takes any binary and
any function list, so other tasks can reuse it.

## Usage

```sh
tools/ghidra/container.sh lib/tbfg.so .scratch/ghidra_out/apply_x_fric.txt \
  0x63b00 apply_x_fric apply_y_fric
cat .scratch/ghidra_out/apply_x_fric.txt
```

Each argument after the output file is either a hex address (`0x63b00`) or
a function name (an exact match on Ghidra's own symbol first; a unique
substring match otherwise, reported if there is more than one). The
output file has one `=== target ===` block per argument: the resolved
function's name, its entry address, and the decompiler's C.

The Ghidra project lives in this worktree's `.scratch/ghidra_project/`:
the first run against a binary imports and auto-analyzes it (`-import`);
every later run against the same binary reuses that analysis
(`-process -noanalysis`), so repeated decompile requests against the same
binary do not re-run Ghidra's full analysis pass each time.

## How it works

- `flake.nix` + `nix/image.nix` build `localhost/hustle-ghidra` with Nix,
  the same way every other tool's image is built:
  `tools/nix_image_build.sh` runs inside the pinned `docker.io/nixos/nix`
  container and loads the result into podman. `build.sh` calls it;
  `container.sh` calls `build.sh` before every run.
- The image holds nixpkgs' `ghidra` package (which provides
  `analyzeHeadless`) and a JDK (Ghidra needs one at run time; nixpkgs'
  package does not bundle one).
- `scripts/DecompileExport.py` is a Ghidra headless post-script (Jython,
  Ghidra's scripting environment): it reads the output path and the
  target list from its own script arguments, resolves each target to a
  `Function` (by address, then by exact or unique-substring name match),
  decompiles it with Ghidra's `DecompInterface`, and writes the C.
- `container.sh` mounts the binary read-only, the output directory
  read-write, and the Ghidra project directory
  (`.scratch/ghidra_project/`) read-write, then runs `analyzeHeadless`
  with that post-script.

## Limits

- A release build's local (non-exported) Rust functions keep no symbol
  of their own once inlined; Ghidra's decompiler reconstructs the
  arithmetic correctly regardless, but names a function only by its
  entry address or by the name of whichever symbol's code it was
  inlined into. Pass the containing function's address or name, and
  read the decompiled output for the inlined logic directly.
- The Jython scripting environment (Ghidra's built-in one) is Python 2.7
  syntax, not Python 3.
