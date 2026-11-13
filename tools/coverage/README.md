# GDScript line coverage

This tool measures which GDScript lines of the game run. It works with Godot
3.5, which has no coverage tool of its own. It runs inside the
`tools/replay_check` container.

## Usage

Add `--coverage DIR` to any `tools/replay_check/container.sh` command:

```sh
tools/replay_check/container.sh --coverage cov/ check --replays replays/
```

The command runs as usual. At the end it writes:

| File | Content |
|---|---|
| `DIR/map.json` | Each probe id, with its file and line |
| `DIR/hits/<job>.json` | The probe counts of one Godot run |
| `DIR/summary.txt` | Covered and total lines, per directory and per file |
| `DIR/lcov.info` | An lcov tracefile. `genhtml` makes an HTML report from it. Its paths are relative to the repository root. |

## How it works

1. `run.py` prepares the work copy as usual: an rsync of the repository and
   the asset import.
2. `instrument.py` rewrites every `.gd` file in the work copy, except under
   `tools/`. It puts a probe in front of each statement in a function body,
   on the same line:

   ```gdscript
   __COV__.h[17] += 1; if x > 0:
   ```

   GDScript 3 accepts a statement and a semicolon in front of simple and
   compound statements (`if`, `for`, `while`, `match`), in static functions
   and in inner classes. A test in the container confirmed this. The line
   numbers do not change, so a `SCRIPT ERROR` in a coverage run points at
   the correct line.
3. `elif`/`else` lines, match patterns and one-line functions get no probe.
   Nothing can go in front of them, and a probe after the header colon does
   not parse: `TYPE_OBJECT: __COV__.h[1] += 1; valid = false` gave "Parse
   Error: Unexpected assign". A body on the next lines is counted. A body on
   the same line as the header is not. The repository has 70 such bodies.
4. `Coverage.gd` becomes the first autoload, named `__COV__`, so the other
   autoloads also count. At exit it writes the counts to the file named in
   `COVERAGE_OUT`. `run_job` sets that variable for each job.
5. `report.py` adds up the counts of all jobs and writes the reports.

The scanner in `instrument.py` tracks strings (including triple-quoted
strings), comments, bracket depth and backslash continuations. So a bracket
or a `#` inside a string does not confuse it.

## Why not an existing tool

The only Godot 3 tool found was
[jamie-pate/godot-code-coverage](https://github.com/jamie-pate/godot-code-coverage)
at commit `e03df3be`, its last Godot 3.5 version. Its `main` branch is
Godot 4. It reloads each script at run time, with each probe on a new line.
On this game, one replay gave about 21,000 `SCRIPT ERROR` lines:

- parse errors after the reload: inherited members not found, and cyclic
  preloads of `ObjectState.gd` and `CharState.gd`,
- an inline `else` that it does not handle,
- `game.gd` failed to load, so no replay ran.

Its line numbers also shift (its issue #3), and it skips most of the code
in autoloads.

## Limits

- A line counts as covered when its first statement starts. A line with
  `if a: b` counts when the `if` runs, not when `b` runs.
- `elif`/`else` headers and match patterns with their body on the next lines
  are not lines in the report. Their bodies are.
- Class-level code (`var x = f()` outside a function) is not counted.
- A Godot process that crashes or is killed writes no counts.
- Mod scripts loaded from zips are not instrumented.
